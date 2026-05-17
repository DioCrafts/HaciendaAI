"""Persistencia del corpus de chunks de manuales AEAT.

Estructura en disco:

    data/manuales/
    └── <fuente>/
        └── <ejercicio_o_undated>/      # "2024" o "undated" si fuente=INFORMA.
            └── <chunk_id_safe>.json

El `chunk_id` lleva caracteres `::` que son seguros en POSIX/NTFS, pero
para evitar problemas en sistemas exóticos los sustituimos por `__` al
nombrar el fichero.

Idempotente por `content_hash`: si el fichero existe con mismo hash,
no se sobrescribe. Protege ediciones humanas (anotaciones, correcciones
manuales del extracto que el chunker no captó bien).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ...models import ManualChunk


@dataclass(frozen=True)
class PersistedChunk:
    """Resultado de persistir un chunk."""

    chunk: ManualChunk
    path: Path
    was_new: bool


def _safe_filename(chunk_id: str) -> str:
    """Convierte `::` en `__` para máxima compatibilidad filesystem."""
    return chunk_id.replace("::", "__").replace("/", "_")


def chunk_path(root: Path, chunk: ManualChunk) -> Path:
    """Ruta canónica del chunk en disco."""
    eje = str(chunk.ejercicio) if chunk.ejercicio is not None else "undated"
    return root / chunk.fuente.value / eje / f"{_safe_filename(chunk.chunk_id)}.json"


def persist_chunk(chunk: ManualChunk, *, root: Path) -> PersistedChunk:
    """Escribe el chunk a disco. `was_new=False` si ya existía con mismo hash."""
    path = chunk_path(root, chunk)
    if path.exists():
        try:
            existing_data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_data = None
        if (
            isinstance(existing_data, dict)
            and existing_data.get("content_hash") == chunk.content_hash
        ):
            return PersistedChunk(chunk=chunk, path=path, was_new=False)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(chunk.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    is_new = not path.exists()
    tmp.replace(path)
    return PersistedChunk(chunk=chunk, path=path, was_new=is_new)


def load_chunk(path: Path) -> ManualChunk:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ManualChunk.from_dict(data)
