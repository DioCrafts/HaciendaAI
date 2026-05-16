"""Persistencia del corpus de jurisprudencia.

Estructura en disco:

    data/jurisprudencia/
    └── <organo>/                      # ts, an, tsj, ap
        └── <año>/                     # 2024, 2025...
            └── ECLI:ES:TS:2024:1234.json

Una sentencia por fichero. Esto:
- Hace los PRs del cron de ingesta muy revisables (1 sentencia ≈ 1 fichero).
- Permite editar metadatos a mano sin tocar nada más.
- Facilita la indexación incremental: cada fichero tiene su `last_fetched_at`.

El nombre del fichero usa el ECLI canónico verbatim. Los `:` son
filesystem-safe en Linux/macOS y en Windows con NTFS si se respetan;
para máxima portabilidad ofrecemos también `sentencia_path_safe` que
reemplaza `:` por `_`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ...models import Organo, Sentencia


@dataclass(frozen=True)
class PersistedSentencia:
    """Resultado de persistir una sentencia."""

    sentencia: Sentencia
    path: Path
    was_new: bool


def _organo_dir(root: Path, organo: Organo) -> Path:
    return root / organo.value


def sentencia_path(root: Path, sentencia: Sentencia) -> Path:
    """Ruta canónica donde guardar/leer la sentencia."""
    return _organo_dir(root, sentencia.organo) / str(sentencia.fecha.year) / f"{sentencia.ecli}.json"


def persist_sentencia(
    sentencia: Sentencia, *, root: Path
) -> PersistedSentencia:
    """Escribe la sentencia a disco. Devuelve `was_new=False` si ya existía.

    Política de actualización: si el fichero existe y el `content_hash`
    coincide, NO se sobrescribe (evita touch de mtime y diff vacío en
    git). Si el hash difiere, se sobrescribe — el revisor verá el cambio
    en el diff.
    """
    path = sentencia_path(root, sentencia)
    if path.exists():
        existing_raw = path.read_text(encoding="utf-8")
        try:
            existing_data = json.loads(existing_raw)
        except json.JSONDecodeError:
            existing_data = None
        if (
            isinstance(existing_data, dict)
            and existing_data.get("content_hash") == sentencia.content_hash
        ):
            return PersistedSentencia(
                sentencia=sentencia, path=path, was_new=False
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    # Escritura atómica: tmp + rename evita ficheros a medias.
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(sentencia.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    is_new = not path.exists()
    tmp.replace(path)
    return PersistedSentencia(sentencia=sentencia, path=path, was_new=is_new)


def load_sentencia(path: Path) -> Sentencia:
    """Carga una sentencia desde su fichero JSON."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return Sentencia.from_dict(data)
