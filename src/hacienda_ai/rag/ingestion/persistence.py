"""Persistencia incremental de normas ingestadas en `data/normas/`.

El corpus se particiona por año de publicación: cada año tiene su propio
fichero `boe_ingested_YYYY.json` en el formato esperado por
`NormaRegistry.from_dict`. Esto:

- Mantiene PRs pequeños y revisables (cada cron diario toca solo el JSON
  del año en curso).
- Evita conflictos entre cron y ediciones manuales en otros años.
- Es transparente para `load_norma_registry`, que ya itera el directorio.

Idempotencia: si una `Norma` con el mismo `boe_id` ya existe, no se duplica
ni se sobreescribe. La función reporta cuántas se añadieron, cuántas se
descartaron por duplicado, y cuántas tuvieron conflicto de hash (el mismo
`boe_id` con `content_hash` distinto al previamente registrado, lo que
sería un cambio en el XML publicado del BOE).

El formato JSON tiene la misma estructura que `lirpf_versions.json`:

    {
      "_meta": {...},
      "normas": [{"boe_id": ..., "kind": ..., "title": ..., "enacted_at": ...}],
      "versions": [{"norma_boe_id": ..., "effective_from": ..., "status": ..., ...}]
    }
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ...models import Norma, SourceKind, VersionNorma
from .norma_builder import BuiltNorma


@dataclass(frozen=True)
class PersistResult:
    """Resumen de una operación de persistencia.

    `added` y `duplicates` son listas de `boe_id` para que el caller pueda
    construir el body del PR y log estructurado. `conflicts` lista los
    `boe_id` cuyo `content_hash` calculado difiere del ya almacenado: caso
    raro pero posible si el BOE corrige una errata; señalado para revisión
    manual.
    """

    added: tuple[str, ...]
    duplicates: tuple[str, ...]
    conflicts: tuple[str, ...]
    path: Path

    @property
    def changed(self) -> bool:
        return bool(self.added) or bool(self.conflicts)


def _partition_path(normas_dir: Path, year: int) -> Path:
    return normas_dir / f"boe_ingested_{year}.json"


PartitionData = dict[str, Any]


def _empty_partition(year: int) -> PartitionData:
    return {
        "_meta": {
            "ingesta": "automatica",
            "fuente": "https://www.boe.es/datosabiertos/api/boe/sumario/",
            "anyo": year,
            "alcance": (
                "Normas con materia fiscal detectadas por el cron diario de "
                "ingesta BOE. Cada entrada se ha clasificado automáticamente "
                "y publicado vía PR para revisión humana antes de mergear. "
                "`effective_from` es aproximación (día siguiente a "
                "publicación) y debe verificarse contra la DF de la norma."
            ),
        },
        "normas": [],
        "versions": [],
    }


def _load_partition(path: Path, year: int) -> PartitionData:
    if not path.exists():
        return _empty_partition(year)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: estructura JSON inesperada")
    data.setdefault("normas", [])
    data.setdefault("versions", [])
    if not isinstance(data["normas"], list) or not isinstance(data["versions"], list):
        raise ValueError(f"{path}: 'normas' y 'versions' deben ser listas")
    return data


def _serialize_norma(norma: Norma) -> dict[str, Any]:
    """Serializa `Norma` a dict JSON-friendly.

    No usamos `dataclasses.asdict` para tener control fino del formato
    (fechas en ISO, enum como `value`).
    """
    return {
        "boe_id": norma.boe_id,
        "kind": (
            norma.kind.value if isinstance(norma.kind, SourceKind) else str(norma.kind)
        ),
        "title": norma.title,
        "enacted_at": norma.enacted_at.isoformat(),
    }


def _serialize_version(v: VersionNorma) -> dict[str, Any]:
    out: dict[str, Any] = {
        "norma_boe_id": v.norma_boe_id,
        "effective_from": v.effective_from.isoformat(),
        "status": v.status.value,
    }
    if v.effective_to is not None:
        out["effective_to"] = v.effective_to.isoformat()
    if v.content_hash is not None:
        out["content_hash"] = v.content_hash
    if v.modified_by_boe_id is not None:
        out["modified_by_boe_id"] = v.modified_by_boe_id
    if v.notes is not None:
        out["notes"] = v.notes
    return out


def persist_built_normas(
    built: list[BuiltNorma],
    *,
    normas_dir: Path,
    today: date | None = None,
) -> list[PersistResult]:
    """Persiste un lote de normas construidas, particionando por año.

    Devuelve una lista de `PersistResult` (uno por partición tocada). Si
    `built` está vacío, devuelve lista vacía sin tocar disco.

    `today` se acepta por compatibilidad con tests deterministas; no se
    usa para decidir la partición (esa la decide `enacted_at` de cada
    norma), solo permite reservar el parámetro si en el futuro queremos
    estampar `_meta.last_run`.
    """
    if not built:
        return []
    del today  # reservado, no usado por ahora.

    # Agrupamos por año del `enacted_at`. Una norma publicada el 1 de enero
    # de 2026 cuyo `enacted_at` (de la regex del título) cae en 2025 va al
    # JSON de 2025, no al de 2026 — esto mantiene la coherencia temporal
    # del corpus.
    by_year: dict[int, list[BuiltNorma]] = {}
    for entry in built:
        by_year.setdefault(entry.norma.enacted_at.year, []).append(entry)

    results: list[PersistResult] = []
    for year in sorted(by_year.keys()):
        entries = by_year[year]
        path = _partition_path(normas_dir, year)
        data = _load_partition(path, year)

        normas_list: list[dict[str, Any]] = data["normas"]
        versions_list: list[dict[str, Any]] = data["versions"]
        existing_norma_ids = {n.get("boe_id") for n in normas_list}
        # Las versiones se indexan por (norma_boe_id, effective_from) porque
        # una misma norma puede tener varias versiones temporales. La
        # ingesta automática solo añade la primera versión por norma; las
        # subsiguientes las introduce un humano.
        existing_versions: dict[tuple[str, str], dict[str, Any]] = {}
        for v in versions_list:
            if isinstance(v, dict):
                key = (
                    str(v.get("norma_boe_id", "")),
                    str(v.get("effective_from", "")),
                )
                existing_versions[key] = v

        added: list[str] = []
        duplicates: list[str] = []
        conflicts: list[str] = []

        for entry in entries:
            norma_dict = _serialize_norma(entry.norma)
            version_dict = _serialize_version(entry.version)
            version_key = (
                str(version_dict["norma_boe_id"]),
                str(version_dict["effective_from"]),
            )
            if entry.norma.boe_id in existing_norma_ids:
                # La norma ya existe. Comprobamos si la versión también está
                # y si el hash coincide.
                prior = existing_versions.get(version_key)
                if prior is not None:
                    prior_hash = prior.get("content_hash")
                    new_hash = version_dict.get("content_hash")
                    if (
                        isinstance(prior_hash, str)
                        and isinstance(new_hash, str)
                        and prior_hash != new_hash
                    ):
                        conflicts.append(entry.norma.boe_id)
                    else:
                        duplicates.append(entry.norma.boe_id)
                else:
                    # Misma norma, versión nueva (no debería pasar en
                    # ingesta automática, pero por completitud).
                    versions_list.append(version_dict)
                    added.append(entry.norma.boe_id)
                continue
            # Norma nueva: añadimos identidad + primera versión.
            normas_list.append(norma_dict)
            versions_list.append(version_dict)
            existing_norma_ids.add(entry.norma.boe_id)
            existing_versions[version_key] = version_dict
            added.append(entry.norma.boe_id)

        if added or conflicts:
            # Ordenamos por boe_id para diffs estables entre ejecuciones
            # del cron en distintos días con normas mezcladas.
            normas_list.sort(key=lambda n: str(n.get("boe_id", "")))
            versions_list.sort(
                key=lambda v: (
                    str(v.get("norma_boe_id", "")),
                    str(v.get("effective_from", "")),
                )
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        results.append(
            PersistResult(
                added=tuple(added),
                duplicates=tuple(duplicates),
                conflicts=tuple(conflicts),
                path=path,
            )
        )
    return results
