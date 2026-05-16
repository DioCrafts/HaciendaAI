"""Persistencia del corpus de resoluciones TEAC/TEAR.

Estructura en disco:

    data/teac_resoluciones/
    └── <organo>/                   # teac, tear, teal
        └── <año>/                  # 2024, 2025...
            └── <numero_safe>.json

Donde `<numero_safe>` es el canónico con `/` reemplazado por `_`
(`00_12345_2023`), porque `/` es ilegal en nombres de fichero.

Idempotente por `content_hash`: si el fichero existe con el mismo hash,
no se sobrescribe — protege ediciones humanas (promoción de
`criterio_confidence` a `manual`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ...models import ResolucionTEAC


@dataclass(frozen=True)
class PersistedResolucion:
    """Resultado de persistir una resolución."""

    resolucion: ResolucionTEAC
    path: Path
    was_new: bool


def _safe_name(canonical: str) -> str:
    return canonical.replace("/", "_")


def consulta_path(root: Path, resolucion: ResolucionTEAC) -> Path:
    """Ruta canónica de la resolución en disco."""
    return (
        root
        / resolucion.organo.value
        / str(resolucion.fecha.year)
        / f"{_safe_name(resolucion.numero)}.json"
    )


def persist_resolucion(
    resolucion: ResolucionTEAC, *, root: Path
) -> PersistedResolucion:
    """Escribe la resolución a disco. `was_new=False` si ya existía con mismo hash."""
    path = consulta_path(root, resolucion)
    if path.exists():
        try:
            existing_data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_data = None
        if (
            isinstance(existing_data, dict)
            and existing_data.get("content_hash") == resolucion.content_hash
        ):
            return PersistedResolucion(
                resolucion=resolucion, path=path, was_new=False
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(resolucion.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    is_new = not path.exists()
    tmp.replace(path)
    return PersistedResolucion(resolucion=resolucion, path=path, was_new=is_new)


def load_resolucion(path: Path) -> ResolucionTEAC:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ResolucionTEAC.from_dict(data)
