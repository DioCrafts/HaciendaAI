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
        # base liquidable alta para que el cap del 10% no haga binding en los
        # tests por defecto. Los tests específicos del cap lo bajan a propósito.
        "taxable_base": {"liquidable": 100_000.0},
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


# ---------- límite del 10% de la base liquidable ----------


def test_donations_capped_at_10_percent_of_base_liquidable() -> None:
    deduction = _load_validated("es_donativos_no_recurrente_2025")
    # Sin cap: 250*0.80 + 750*0.40 = 500. Con base liquidable de 3.000 €, el
    # 10% = 300 € actúa como tope. La deducción se recorta de 500 a 300.
    profile = _profile(
        expenses={"donations_amount": 1000.0},
        taxable_base={"liquidable": 3000.0},
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "applies"
    assert result.estimated_amount == 300.0


def test_donations_not_capped_when_base_liquidable_is_high() -> None:
    deduction = _load_validated("es_donativos_no_recurrente_2025")
    profile = _profile(
        expenses={"donations_amount": 1000.0},
        taxable_base={"liquidable": 100_000.0},
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "applies"
    assert result.estimated_amount == 500.0  # 250*0.80 + 750*0.40


def test_donations_missing_base_liquidable_returns_missing_data() -> None:
    deduction = _load_validated("es_donativos_no_recurrente_2025")
    profile = _profile(expenses={"donations_amount": 500.0}, taxable_base={})
    result = evaluate_deduction(deduction, profile)
    assert result.status == "missing_data"
    assert result.missing_fields == ("taxable_base.liquidable",)
    assert "límite legal" in result.reason


def test_lote2_rules_are_validada_in_corpus() -> None:
    """Las dos reglas de donativos quedan en validada tras la sesión de
    promoción de mayo de 2026: porcentajes 80/40/45 % y cap del 10 %
    están modelados; las sutilezas no modeladas (donativos en especie,
    +5 puntos por actividades prioritarias de mecenazgo) quedan
    documentadas en la descripción."""
    deductions = {d.id: d for d in load_deductions()}
    for rule_id in ("es_donativos_no_recurrente_2025", "es_donativos_recurrente_2025"):
        deduction = deductions[rule_id]
        assert deduction.validation_status == ValidationStatus.VALIDADA, (
            f"{rule_id}: esperado validada, encontrado {deduction.validation_status.value}"
        )
        assert deduction.last_reviewed_at is not None
        assert all(source.checked_at is not None for source in deduction.sources)
