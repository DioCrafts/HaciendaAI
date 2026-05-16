"""Snapshot persistente de hashes por artículo de una norma consolidada.

Cada norma del registry genera un fichero
`data/normas/snapshots/<boe_id>.json` con la huella SHA-256 de cada
bloque precepto en su versión vigente a la fecha de la última
verificación. La estructura JSON es:

    {
      "boe_id": "BOE-A-2006-20764",
      "last_checked_at": "2026-05-16",
      "reference_date": "2026-05-16",
      "consolidated_articles": {
        "a1": "<sha256>",
        "a2": "<sha256>",
        "a81bis": "<sha256>"
      }
    }

Estos snapshots SE COMMITEAN al repo: el diff de un PR muestra
exactamente qué artículos han cambiado entre dos pasadas del cron. Solo
contienen hashes (no texto), así que el tamaño es modesto (~7 KB para
LIRPF con 110 artículos).

`last_checked_at` y `reference_date` se separan porque el caller puede
pedir "snapshot del consolidado vigente el 1 de enero de 2024" mientras
ejecuta el cron hoy. Por defecto coinciden.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any


class SnapshotError(ValueError):
    """Snapshot JSON corrupto o con campos inválidos."""


@dataclass(frozen=True)
class NormaSnapshot:
    """Huella de los artículos consolidados de una norma a una fecha.

    `consolidated_articles` mapea `block_id` → SHA-256 hex. Solo se
    incluyen bloques con versión vigente en `reference_date`; bloques
    derogados antes de esa fecha o aún no vigentes se omiten.
    """

    boe_id: str
    last_checked_at: date
    reference_date: date
    consolidated_articles: dict[str, str] = field(default_factory=dict)

    @property
    def article_ids(self) -> set[str]:
        return set(self.consolidated_articles)

    def to_json(self) -> dict[str, Any]:
        return {
            "boe_id": self.boe_id,
            "last_checked_at": self.last_checked_at.isoformat(),
            "reference_date": self.reference_date.isoformat(),
            # Ordenamos para tener diffs estables entre ejecuciones del cron.
            "consolidated_articles": dict(
                sorted(self.consolidated_articles.items())
            ),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "NormaSnapshot":
        try:
            boe_id = data["boe_id"]
            last_checked_at = date.fromisoformat(data["last_checked_at"])
            reference_date = date.fromisoformat(data["reference_date"])
            articles_raw = data["consolidated_articles"]
        except KeyError as exc:
            raise SnapshotError(f"campo obligatorio ausente: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise SnapshotError(f"snapshot inválido: {exc}") from exc
        if not isinstance(articles_raw, dict):
            raise SnapshotError("consolidated_articles debe ser un objeto")
        articles: dict[str, str] = {}
        for k, v in articles_raw.items():
            if not isinstance(k, str) or not isinstance(v, str):
                raise SnapshotError(
                    f"entrada inválida en consolidated_articles: {k!r}={v!r}"
                )
            normalized = v.lower()
            if len(normalized) != 64 or not all(
                c in "0123456789abcdef" for c in normalized
            ):
                raise SnapshotError(
                    f"hash inválido para bloque {k!r}: {v!r} (esperado SHA-256 hex)"
                )
            articles[k] = normalized
        return cls(
            boe_id=boe_id,
            last_checked_at=last_checked_at,
            reference_date=reference_date,
            consolidated_articles=articles,
        )


def snapshot_path(snapshots_dir: Path, boe_id: str) -> Path:
    """Ruta canónica del snapshot de una norma.

    Usamos el `boe_id` verbatim como nombre de archivo: es seguro porque
    el formato BOE-A-YYYY-NNNNN no contiene caracteres problemáticos
    para filesystems.
    """
    return snapshots_dir / f"{boe_id}.json"


def load_snapshot(snapshots_dir: Path, boe_id: str) -> NormaSnapshot | None:
    """Devuelve el snapshot previo de la norma, o `None` si nunca se ha hecho.

    Lanza `SnapshotError` si el fichero existe pero está corrupto: en
    ese caso el caller debe parar y avisar (no asumir bootstrap), porque
    perder un snapshot existente equivale a perder la línea base de
    detección de cambios.
    """
    path = snapshot_path(snapshots_dir, boe_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SnapshotError(f"{path}: JSON inválido: {exc}") from exc
    if not isinstance(data, dict):
        raise SnapshotError(f"{path}: raíz del JSON no es objeto")
    return NormaSnapshot.from_json(data)


def save_snapshot(snapshots_dir: Path, snapshot: NormaSnapshot) -> Path:
    """Escribe (o reemplaza) el snapshot de una norma. Devuelve la ruta.

    Crea el directorio si no existe. Escribe atómicamente vía rename
    para evitar dejar un fichero a medias si el proceso muere.
    """
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(snapshots_dir, snapshot.boe_id)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(snapshot.to_json(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path
