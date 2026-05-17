"""Persistencia del timeline completo por artículo de una norma.

Complementa `NormaSnapshot` (que persiste solo hashes a una fecha): aquí
guardamos las versiones COMPLETAS con su texto literal, para que
`ArticleRegistry` pueda reconstruirse offline y responder
"¿qué decía art. 23 LIRPF el 1 de marzo de 2023?" sin necesidad de
volver a descargar y parsear el XML consolidado.

Ubicación canónica: `data/normas/article_versions/<boe_id>.json`. Es
un fichero por norma, no un agregado, por dos razones:
1. **Diff legible en PRs**: cuando un cron detecta drift por artículo,
   el PR muestra exactamente qué versiones cambian de qué norma.
2. **Carga selectiva**: si el sistema solo necesita LIRPF, no carga 100
   timelines de normas no relacionadas.

Estructura JSON:

    {
      "boe_id": "BOE-A-2006-20764",
      "last_checked_at": "2026-05-17",
      "reference_date": "2026-05-17",
      "versions": [
        {
          "norma_boe_id": "BOE-A-2006-20764",
          "article_id": "a23",
          "effective_from": "2007-01-01",
          "effective_to": "2014-12-31",
          "text": "...",
          "text_hash": "sha256-hex",
          "modified_by_boe_id": "BOE-A-2014-12328"
        },
        ...
      ]
    }

Tamaño esperado: ~500 KB por LIRPF completa (110 preceptos × ~3
versiones × ~1.5 KB de texto). Aceptable para repo, pero no para
incluir en cada commit — por eso solo se actualizan tras detectar
drift, no en cada cron.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ...models import VersionArticulo


class ArticleSnapshotError(ValueError):
    """Snapshot por artículo corrupto o con campos inválidos."""


@dataclass(frozen=True)
class ArticleVersionSnapshot:
    """Timeline completo por artículo de una norma a una fecha.

    `versions` es la lista plana de `VersionArticulo` (todas las
    versiones de todos los artículos). Mantenerla plana facilita el
    diff y la deduplicación; cuando se necesita acceso indexado, el
    caller convierte a `ArticleRegistry`.
    """

    boe_id: str
    last_checked_at: date
    reference_date: date
    versions: tuple[VersionArticulo, ...] = field(default_factory=tuple)

    @property
    def article_ids(self) -> set[str]:
        return {v.article_id for v in self.versions}

    @property
    def total_versions(self) -> int:
        return len(self.versions)

    def to_json(self) -> dict[str, Any]:
        return {
            "boe_id": self.boe_id,
            "last_checked_at": self.last_checked_at.isoformat(),
            "reference_date": self.reference_date.isoformat(),
            # Orden estable para diffs reproducibles entre ejecuciones:
            # (article_id, effective_from). El article_id sigue el orden
            # natural del bloque BOE (a1, a2, a10, a81bis...) si se hace
            # con sorted normal por string — aceptable para diff legible.
            "versions": [
                v.to_dict()
                for v in sorted(
                    self.versions,
                    key=lambda v: (v.article_id, v.effective_from),
                )
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "ArticleVersionSnapshot":
        try:
            boe_id = data["boe_id"]
            last_checked_at = date.fromisoformat(data["last_checked_at"])
            reference_date = date.fromisoformat(data["reference_date"])
            versions_raw = data["versions"]
        except KeyError as exc:
            raise ArticleSnapshotError(
                f"campo obligatorio ausente: {exc}"
            ) from exc
        except (TypeError, ValueError) as exc:
            raise ArticleSnapshotError(
                f"article snapshot inválido: {exc}"
            ) from exc
        if not isinstance(versions_raw, list):
            raise ArticleSnapshotError(
                "versions debe ser lista de objetos VersionArticulo"
            )
        versions = tuple(VersionArticulo.from_dict(raw) for raw in versions_raw)
        return cls(
            boe_id=boe_id,
            last_checked_at=last_checked_at,
            reference_date=reference_date,
            versions=versions,
        )


def article_snapshot_path(snapshots_dir: Path, boe_id: str) -> Path:
    """Ruta canónica del snapshot por artículo de una norma."""
    return snapshots_dir / f"{boe_id}.json"


def load_article_snapshot(
    snapshots_dir: Path, boe_id: str
) -> ArticleVersionSnapshot | None:
    """Carga el snapshot previo si existe. Lanza si está corrupto.

    Mismo criterio que `load_snapshot` en `snapshot.py`: nunca asumir
    bootstrap por corrupción — perder un snapshot existente equivale
    a perder la línea base de detección de cambios por artículo.
    """
    path = article_snapshot_path(snapshots_dir, boe_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ArticleSnapshotError(
            f"{path}: JSON inválido: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ArticleSnapshotError(
            f"{path}: raíz del JSON no es objeto"
        )
    return ArticleVersionSnapshot.from_json(data)


def save_article_snapshot(
    snapshots_dir: Path, snapshot: ArticleVersionSnapshot
) -> Path:
    """Persiste atómicamente el snapshot por artículo y devuelve la ruta."""
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    path = article_snapshot_path(snapshots_dir, snapshot.boe_id)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(snapshot.to_json(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
    return path


__all__ = [
    "ArticleSnapshotError",
    "ArticleVersionSnapshot",
    "article_snapshot_path",
    "load_article_snapshot",
    "save_article_snapshot",
]
