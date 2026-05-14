"""Tests fiscales de las bonificaciones Ceuta/Melilla (art. 68.4 LIRPF).

Las reglas están en pendiente_tests: el porcentaje del 60 % es estable
pero la *atribución* de cuota requiere análisis caso a caso. Los tests
verifican el comportamiento del motor con flip local a VALIDADA.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import Deduction, TaxProfile, ValidationStatus
from hacienda_ai.rules import evaluate_deduction, evaluate_deductions


def _load_validated(deduction_id: str) -> Deduction:
    deductions = {d.id: d for d in load_deductions()}
    return replace(deductions[deduction_id], validation_status=ValidationStatus.VALIDADA)


def _profile(region: str, **overrides: Any) -> TaxProfile:
    data: dict[str, Any] = {
        "tax_year": 2025,
        "region": region,
        "income": {"work_income": 30000.0},
        "cuota": {"attributable_to_ceuta_melilla": 4000.0},
        "documents": [
            "Certificado de residencia o documentación que acredite la obtención de rentas en Ceuta",
            "Certificado de residencia o documentación que acredite la obtención de rentas en Melilla",
        ],
    }
    data.update(overrides)
    return TaxProfile.from_dict(data)


# ---------- es_bonificacion_ceuta_2025 ----------


def test_ceuta_bonification_applies_60_percent() -> None:
    deduction = _load_validated("es_bonificacion_ceuta_2025")
    result = evaluate_deduction(deduction, _profile("Ceuta"))
    assert result.status == "applies"
    assert result.estimated_amount == 2400.0  # 4000 * 0.60


def test_ceuta_bonification_does_not_apply_to_other_regions() -> None:
    deduction = _load_validated("es_bonificacion_ceuta_2025")
    for region in ("Madrid", "Melilla", "Cataluña"):
        result = evaluate_deduction(deduction, _profile(region))
        assert result.status == "does_not_apply", (
            f"region={region!r}: esperado does_not_apply, obtenido {result.status}"
        )


def test_ceuta_bonification_returns_missing_data_without_attributable_cuota() -> None:
    deduction = _load_validated("es_bonificacion_ceuta_2025")
    result = evaluate_deduction(deduction, _profile("Ceuta", cuota={}))
    assert result.status == "missing_data"
    assert "cuota.attributable_to_ceuta_melilla" in result.missing_fields


def test_ceuta_bonification_missing_evidence() -> None:
    deduction = _load_validated("es_bonificacion_ceuta_2025")
    result = evaluate_deduction(deduction, _profile("Ceuta", documents=[]))
    assert result.status == "missing_evidence"
    assert result.estimated_amount == 2400.0


# ---------- es_bonificacion_melilla_2025 ----------


def test_melilla_bonification_applies_60_percent() -> None:
    deduction = _load_validated("es_bonificacion_melilla_2025")
    result = evaluate_deduction(deduction, _profile("Melilla"))
    assert result.status == "applies"
    assert result.estimated_amount == 2400.0


def test_melilla_bonification_does_not_apply_to_ceuta_or_other_regions() -> None:
    deduction = _load_validated("es_bonificacion_melilla_2025")
    for region in ("Ceuta", "Madrid"):
        result = evaluate_deduction(deduction, _profile(region))
        assert result.status == "does_not_apply"


# ---------- incompatibilidad mutua ----------


def test_ceuta_and_melilla_are_incompatible_when_both_evaluable() -> None:
    """Un contribuyente no puede ser residente de ambas a la vez. Aun así,
    si por error ambos rules salieran como applies, la incompatibilidad
    declarada debe dejar sólo uno."""
    ceuta = _load_validated("es_bonificacion_ceuta_2025")
    melilla = _load_validated("es_bonificacion_melilla_2025")
    # Construimos un profile irreal (region=Ceuta) — sólo Ceuta pasará el
    # filtro de región; Melilla quedará does_not_apply por el filtro.
    results = evaluate_deductions([ceuta, melilla], _profile("Ceuta"))
    by_id = {r.deduction_id: r for r in results}
    assert by_id["es_bonificacion_ceuta_2025"].status == "applies"
    assert by_id["es_bonificacion_melilla_2025"].status == "does_not_apply"


# ---------- estado pendiente_tests en el corpus ----------


def test_bonifications_are_marked_pendiente_tests_in_corpus() -> None:
    """Verificamos explícitamente que NO están en validada: la atribución
    de cuota requiere análisis fiscal humano que el motor no realiza."""
    deductions = {d.id: d for d in load_deductions()}
    for rule_id in ("es_bonificacion_ceuta_2025", "es_bonificacion_melilla_2025"):
        deduction = deductions[rule_id]
        assert deduction.validation_status == ValidationStatus.PENDIENTE_TESTS
