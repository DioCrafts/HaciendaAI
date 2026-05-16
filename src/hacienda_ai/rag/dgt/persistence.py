"""Persistencia del corpus de consultas DGT.

Estructura en disco:

    data/dgt_consultas/
    └── <año>/                 # 2024, 2025...
        └── V<NNNN>-<YY>.json

Una consulta por fichero. Particionar por año mantiene PRs pequeños y
permite editar metadatos a mano sin riesgo. Indexamos por número (no
por impuesto): un mismo número es único globalmente, y la mayoría de
consultas con criterio jurisprudencial relevante para una respuesta del
LLM se localizan por número, no por impuesto.

Idempotente por `content_hash`: si el fichero existe y el hash de la
fuente coincide, no se sobrescribe. Esto protege ediciones humanas
(promoción de `criterio_confidence` a `manual`, ajuste manual del
`criterio` extraído).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ...models import ConsultaDGT


@dataclass(frozen=True)
class PersistedConsulta:
    """Resultado de persistir una consulta."""

    consulta: ConsultaDGT
    path: Path
    was_new: bool


def consulta_path(root: Path, consulta: ConsultaDGT) -> Path:
    """Ruta canónica de la consulta en disco."""
    return root / str(consulta.fecha_salida.year) / f"{consulta.numero}.json"


def persist_consulta(
    consulta: ConsultaDGT, *, root: Path
) -> PersistedConsulta:
    """Escribe la consulta a disco. `was_new=False` si ya existía con mismo hash.

    Política: si el fichero existe y `content_hash` coincide, no toca el
    disco (protege ediciones humanas). Si difiere, sobrescribe — el
    revisor verá el cambio en el diff.
    """
    path = consulta_path(root, consulta)
    if path.exists():
        existing_raw = path.read_text(encoding="utf-8")
        try:
            existing_data = json.loads(existing_raw)
        except json.JSONDecodeError:
            existing_data = None
        if (
            isinstance(existing_data, dict)
            and existing_data.get("content_hash") == consulta.content_hash
        ):
            return PersistedConsulta(
                consulta=consulta, path=path, was_new=False
            )

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(consulta.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    is_new = not path.exists()
    tmp.replace(path)
    return PersistedConsulta(consulta=consulta, path=path, was_new=is_new)


def load_consulta(path: Path) -> ConsultaDGT:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ConsultaDGT.from_dict(data)
