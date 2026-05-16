"""Golden dataset fiscal: perfiles sintéticos con resultado esperado.

Cada archivo `tests/golden/*.json` describe un caso (perfil + aplicaciones
esperadas con importe). El motor evalúa el perfil sobre el corpus completo
y este runner verifica:

1. El SET de deducciones con `status=applies` coincide EXACTAMENTE con
   `expected.applies`. Ni una más, ni una menos. Si añades una deducción
   al corpus que aplica para uno de estos perfiles, este test rompe y te
   obliga a actualizar el oracle conscientemente.
2. Cada `applies` esperada lleva el `estimated_amount` exacto declarado.
   Si el motor empieza a devolver otro número (refactor, bug, norma
   cambiada vía verify-seed), el test te dice cuál y con qué diferencia.
3. El SET de deducciones con `status=requires_manual_calculation` coincide
   EXACTAMENTE con `expected.requires_manual_calculation`. Misma lógica.
4. Las deducciones listadas en `expected.missing_data_includes` /
   `does_not_apply_includes` están al menos en esos estados (subset, no
   exacto: la lista es un *check*, no una *clausura*).

Cuando una norma cambie legítimamente (BOE marca drift y refrescamos
hashes/importes), el flujo es:
    1. `python scripts/verify_seed.py --update` actualiza el corpus.
    2. Los goldens afectados rompen aquí con diff claro.
    3. Se actualizan los expected y el PR documenta el cambio normativo.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import TaxProfile
from hacienda_ai.normas import load_norma_registry
from hacienda_ai.rules import evaluate_deductions

GOLDEN_DIR = Path(__file__).parent / "golden"


def _golden_cases() -> list[Path]:
    """Recoge cada JSON del directorio. Los archivos `_*.json` (prefijo
    underscore) se reservan para documentación/utilidades y se excluyen."""
    return sorted(p for p in GOLDEN_DIR.glob("*.json") if not p.name.startswith("_"))


@pytest.mark.parametrize("case_path", _golden_cases(), ids=lambda p: p.stem)
def test_golden_case(case_path: Path) -> None:
    raw = json.loads(case_path.read_text(encoding="utf-8"))
    profile = TaxProfile.from_dict(raw["profile"])
    deductions = load_deductions()
    registry = load_norma_registry()
    evaluations = evaluate_deductions(deductions, profile, registry)
    by_id = {ev.deduction_id: ev for ev in evaluations}

    expected = raw["expected"]

    # 1. Conjunto exacto de `applies` con importes.
    expected_applies = {item["deduction_id"]: item for item in expected.get("applies", [])}
    actual_applies = {ev.deduction_id: ev for ev in evaluations if ev.status == "applies"}

    unexpected = sorted(set(actual_applies) - set(expected_applies))
    missing = sorted(set(expected_applies) - set(actual_applies))
    assert not unexpected and not missing, _format_set_diff(
        case_path, "applies", expected_applies.keys(), actual_applies.keys()
    )

    amount_mismatches: list[str] = []
    for ded_id, item in expected_applies.items():
        actual_amount = actual_applies[ded_id].estimated_amount
        expected_amount = float(item["amount"])
        if actual_amount != expected_amount:
            amount_mismatches.append(
                f"  {ded_id}: esperado {expected_amount:.2f} €, "
                f"calculado {actual_amount:.2f} € (Δ {actual_amount - expected_amount:+.2f})"
            )
    assert not amount_mismatches, (
        f"{case_path.name}: importes divergentes en `applies`:\n"
        + "\n".join(amount_mismatches)
    )

    # 2. Conjunto exacto de `requires_manual_calculation` (sin importes,
    #    por definición la rama no cuantifica).
    expected_rmc = set(expected.get("requires_manual_calculation", []))
    actual_rmc = {ev.deduction_id for ev in evaluations
                  if ev.status == "requires_manual_calculation"}
    assert expected_rmc == actual_rmc, _format_set_diff(
        case_path, "requires_manual_calculation", expected_rmc, actual_rmc
    )

    # 3. Subsets opcionales para verificar señales negativas concretas
    #    sin amarrarnos al tamaño total del corpus.
    for state_name, key in (
        ("missing_data", "missing_data_includes"),
        ("missing_evidence", "missing_evidence_includes"),
        ("does_not_apply", "does_not_apply_includes"),
    ):
        expected_ids = expected.get(key, [])
        for ded_id in expected_ids:
            ev = by_id.get(ded_id)
            assert ev is not None, (
                f"{case_path.name}: `{key}` referencia id desconocido `{ded_id}`"
            )
            assert ev.status == state_name, (
                f"{case_path.name}: esperaba `{ded_id}` en estado `{state_name}`, "
                f"motor devolvió `{ev.status}` (importe {ev.estimated_amount} €)"
            )


def _format_set_diff(
    case_path: Path, label: str, expected: Any, actual: Any
) -> str:
    expected_set = set(expected)
    actual_set = set(actual)
    only_expected = sorted(expected_set - actual_set)
    only_actual = sorted(actual_set - expected_set)
    return (
        f"{case_path.name}: conjuntos `{label}` no coinciden.\n"
        f"  Esperadas que el motor NO está aplicando:\n    "
        + ("\n    ".join(only_expected) if only_expected else "—")
        + "\n  El motor está aplicando inesperadamente:\n    "
        + ("\n    ".join(only_actual) if only_actual else "—")
    )


def test_golden_dataset_exists() -> None:
    """El golden no puede borrarse silenciosamente: este test obliga a
    que existan ≥10 casos. Si bajas de aquí, has perdido cobertura."""
    cases = _golden_cases()
    assert len(cases) >= 10, (
        f"golden dataset tiene solo {len(cases)} casos; mínimo esperado 10"
    )


def test_golden_cases_have_unique_names() -> None:
    """Cada caso lleva un `name` único en su payload, útil para reportes
    y para evitar collisiones cuando se importan en otros runners."""
    names = []
    for path in _golden_cases():
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert "name" in raw, f"{path.name}: falta campo `name`"
        names.append(raw["name"])
    assert len(names) == len(set(names)), (
        f"nombres duplicados en golden dataset: "
        f"{sorted([n for n in names if names.count(n) > 1])}"
    )
