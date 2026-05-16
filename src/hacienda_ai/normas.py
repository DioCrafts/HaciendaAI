"""Carga del catálogo de normas y versiones temporales.

Pareja simétrica de `deductions.py`: lee uno o más JSON desde
`data/normas/` y construye un `NormaRegistry` con identidad estable de
cada norma y sus versiones sin solapamientos. El registro resultante se
inyecta en el motor de reglas para responder a "¿qué redacción estaba
viva en el devengo?" sin reescribir reglas.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import NormaRegistry, ValidationError

DEFAULT_NORMAS_DIR = Path(__file__).parent / "data" / "normas"


def load_norma_registry(path: Path | str = DEFAULT_NORMAS_DIR) -> NormaRegistry:
    """Carga normas y versiones desde uno o varios JSON y devuelve el registry.

    Cada archivo debe tener el formato esperado por `NormaRegistry.from_dict`:
    `{"normas": [...], "versions": [...]}` (claves desconocidas como `_meta`
    se ignoran). Si la ruta es un directorio, se concatena el contenido de
    todos los `*.json` ordenados por nombre antes de construir el registry,
    de modo que cualquier solapamiento o referencia a norma desconocida
    aflora con el error de validación de `NormaRegistry`.
    """
    root = Path(path)
    files = [root] if root.is_file() else sorted(root.glob("*.json"))
    combined_normas: list[Any] = []
    combined_versions: list[Any] = []
    for file_path in files:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValidationError(
                f"{file_path}: el JSON de normas debe ser un objeto con claves "
                "'normas' y 'versions'"
            )
        normas = raw.get("normas", [])
        versions = raw.get("versions", [])
        if not isinstance(normas, list) or not isinstance(versions, list):
            raise ValidationError(
                f"{file_path}: 'normas' y 'versions' deben ser listas"
            )
        combined_normas.extend(normas)
        combined_versions.extend(versions)
    return NormaRegistry.from_dict(
        {"normas": combined_normas, "versions": combined_versions}
    )
