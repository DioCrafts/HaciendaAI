"""Tests fiscales del lote 2: donativos Ley 49/2002 art. 19.

Como en el lote 1, las reglas viven en el JSON como 'pendiente_tests'.
Hacemos flip local a VALIDADA con dataclasses.replace para verificar el
motor — el JSON no se toca. Cuando los importes (80% / 250 € / 40% / 45%)
y el régimen recurrente queden contrastados manualmente contra el Manual
práctico de Renta AEAT 2025 y la Ley 7/2024, basta cambiar
validation_status en el JSON.
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


def _profile(**overrides: Any) -> TaxProfile:
    data: dict[str, Any] = {
        "tax_year": 2025,
        "region": "Madrid",
        "income": {"work_income": 30000.0},
        "expenses": {},
        "documents": ["Certificado de donativo expedido por la entidad beneficiaria"],
    }
    data.update(overrides)
    return TaxProfile.from_dict(data)


# ---------- es_donativos_no_recurrente_2025 ----------


def test_donations_non_recurrent_under_threshold_uses_80_percent() -> None:
    deduction = _load_validated("es_donativos_no_recurrente_2025")
    result = evaluate_deduction(deduction, _profile(expenses={"donations_amount": 100.0}))
    assert result.status == "applies"
    assert result.estimated_amount == 80.0


def test_donations_non_recurrent_at_threshold_applies_full_first_tier() -> None:
    deduction = _load_validated("es_donativos_no_recurrente_2025")
    result = evaluate_deduction(deduction, _profile(expenses={"donations_amount": 250.0}))
    assert result.status == "applies"
    assert result.estimated_amount == 200.0


def test_donations_non_recurrent_above_threshold_combines_both_tiers() -> None:
    deduction = _load_validated("es_donativos_no_recurrente_2025")
    result = evaluate_deduction(deduction, _profile(expenses={"donations_amount": 500.0}))
    # 250 * 0.80 + 250 * 0.40 = 200 + 100 = 300
    assert result.status == "applies"
    assert result.estimated_amount == 300.0


def test_donations_non_recurrent_missing_evidence() -> None:
    deduction = _load_validated("es_donativos_no_recurrente_2025")
    result = evaluate_deduction(deduction, _profile(expenses={"donations_amount": 500.0}, documents=[]))
    assert result.status == "missing_evidence"
    assert result.estimated_amount == 300.0


# ---------- es_donativos_recurrente_2025 ----------


def test_donations_recurrent_requires_qualifying_flag() -> None:
    deduction = _load_validated("es_donativos_recurrente_2025")
    profile = _profile(
        expenses={"donations_amount": 500.0},
        documents=[
            "Certificado de donativo expedido por la entidad beneficiaria",
            "Justificación de donativos a la misma entidad en los dos ejercicios anteriores por importe igual o superior",
        ],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "missing_data"
    assert "personal.donations_recurrent_qualifying" in result.missing_fields


def test_donations_recurrent_does_not_apply_when_flag_is_false() -> None:
    deduction = _load_validated("es_donativos_recurrente_2025")
    profile = _profile(
        personal={"donations_recurrent_qualifying": False},
        expenses={"donations_amount": 500.0},
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "does_not_apply"


def test_donations_recurrent_above_threshold_uses_45_percent() -> None:
    deduction = _load_validated("es_donativos_recurrente_2025")
    profile = _profile(
        personal={"donations_recurrent_qualifying": True},
        expenses={"donations_amount": 500.0},
        documents=[
            "Certificado de donativo expedido por la entidad beneficiaria",
            "Justificación de donativos a la misma entidad en los dos ejercicios anteriores por importe igual o superior",
        ],
    )
    result = evaluate_deduction(deduction, profile)
    # 250 * 0.80 + 250 * 0.45 = 200 + 112.50 = 312.50
    assert result.status == "applies"
    assert result.estimated_amount == 312.50


# ---------- incompatibilidad entre los dos regímenes ----------


def test_recurrent_wins_over_non_recurrent_when_both_apply() -> None:
    non_recurrent = _load_validated("es_donativos_no_recurrente_2025")
    recurrent = _load_validated("es_donativos_recurrente_2025")
    profile = _profile(
        personal={"donations_recurrent_qualifying": True},
        expenses={"donations_amount": 1000.0},
        documents=[
            "Certificado de donativo expedido por la entidad beneficiaria",
            "Justificación de donativos a la misma entidad en los dos ejercicios anteriores por importe igual o superior",
        ],
    )
    results = evaluate_deductions([non_recurrent, recurrent], profile)
    by_id = {r.deduction_id: r for r in results}
    # Non-recurrent: 250*0.80 + 750*0.40 = 500
    # Recurrent:   250*0.80 + 750*0.45 = 537.50
    assert by_id["es_donativos_recurrente_2025"].status == "applies"
    assert by_id["es_donativos_recurrente_2025"].estimated_amount == 537.50
    assert by_id["es_donativos_no_recurrente_2025"].status == "does_not_apply"
    assert by_id["es_donativos_no_recurrente_2025"].estimated_amount == 0.0
    assert "Incompatible con" in by_id["es_donativos_no_recurrente_2025"].reason
