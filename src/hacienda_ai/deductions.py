"""Carga y validación de la base normalizada de deducciones."""

from __future__ import annotations

import json
from pathlib import Path

from .models import Deduction, ValidationError

DEFAULT_DEDUCTIONS_DIR = Path(__file__).parent / "data" / "deductions"


def load_deductions(path: Path | str = DEFAULT_DEDUCTIONS_DIR) -> list[Deduction]:
    """Carga deducciones desde JSON y valida identificadores duplicados."""
    root = Path(path)
    files = [root] if root.is_file() else sorted(root.glob("*.json"))
    deductions: list[Deduction] = []
    seen: set[str] = set()
    for file_path in files:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        entries = raw if isinstance(raw, list) else raw.get("deductions", [])
        if not isinstance(entries, list):
            raise ValidationError(f"{file_path}: el JSON debe contener una lista de deducciones")
        for entry in entries:
            deduction = Deduction.from_dict(entry)
            if deduction.id in seen:
                raise ValidationError(f"Deducción duplicada: {deduction.id}")
            seen.add(deduction.id)
            deductions.append(deduction)
    return deductions
