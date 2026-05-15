"""Tests del detector proactivo de oportunidades fiscales."""

from __future__ import annotations

from typing import Any

from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import TaxProfile
from hacienda_ai.opportunities import detect_opportunities
from hacienda_ai.rules import evaluate_deductions


def _profile(**overrides: Any) -> TaxProfile:
    data: dict[str, Any] = {
        "tax_year": 2025,
        "region": "Madrid",
        "personal": {"age": 35},
        "income": {"work_income": 35000.0},
        "withholdings": [{"amount": 5800.0}],
        "taxable_base": {
            "general": 35000.0,
            "savings": 0.0,
            "liquidable": 35000.0,
            "net_work_and_economic_income": 32000.0,
        },
    }
    data.update(overrides)
    return TaxProfile.from_dict(data)


def test_detect_returns_opportunities_when_data_is_missing() -> None:
    deductions = load_deductions()
    profile = _profile()
    evaluations = evaluate_deductions(deductions, profile)
    opportunities = detect_opportunities(profile, deductions, evaluations)
    assert len(opportunities) > 0
    ids = [opportunity.deduction_id for opportunity in opportunities]
    # Reglas estatales validadas que deberían aparecer cuando no hay datos:
    assert "es_aportaciones_plan_pensiones_individual_2025" in ids
    assert "es_donativos_no_recurrente_2025" in ids


def test_opportunities_are_sorted_by_savings_descending() -> None:
    deductions = load_deductions()
    profile = _profile()
    evaluations = evaluate_deductions(deductions, profile)
    opportunities = detect_opportunities(profile, deductions, evaluations)
    savings = [opportunity.potential_savings_estimate for opportunity in opportunities]
    assert savings == sorted(savings, reverse=True), "no están ordenadas por ahorro descendente"


def test_pension_plan_individual_opportunity_savings_match_marginal_rate() -> None:
    """Para un perfil con base 35.000 € (tipo marginal 30 %), rellenar el
    plan de pensiones con 1.500 € debería ahorrar ≈ 450 € en cuota
    diferencial."""
    deductions = load_deductions()
    profile = _profile()
    evaluations = evaluate_deductions(deductions, profile)
    opportunities = detect_opportunities(profile, deductions, evaluations)
    pension = next(
        opportunity
        for opportunity in opportunities
        if opportunity.deduction_id == "es_aportaciones_plan_pensiones_individual_2025"
    )
    assert abs(pension.potential_savings_estimate - 450.0) < 0.01
    assert "expenses.pension_plan_contribution_amount" in pension.missing_fields


def test_donations_opportunity_uses_tiered_calculation() -> None:
    """Donativos no recurrente: con synthetic 250 € se aplica 80 % → ahorro
    real ≈ 200 €. Pero como el synthetic usa `limit` y la regla no tiene
    `limit`, usa el DEFAULT_SYNTHETIC_AMOUNT (1.000 €): 250*0.8 + 750*0.4 = 500."""
    deductions = load_deductions()
    profile = _profile()
    evaluations = evaluate_deductions(deductions, profile)
    opportunities = detect_opportunities(profile, deductions, evaluations)
    donations = next(
        opportunity for opportunity in opportunities if opportunity.deduction_id == "es_donativos_no_recurrente_2025"
    )
    assert abs(donations.potential_savings_estimate - 500.0) < 0.01


def test_pendiente_rules_do_not_appear() -> None:
    """Las reglas autonómicas placeholder (pendiente_fuente) no entran en
    sugerencias para no recomendar reglas no auditadas."""
    deductions = load_deductions()
    profile = _profile()
    evaluations = evaluate_deductions(deductions, profile)
    opportunities = detect_opportunities(profile, deductions, evaluations)
    autonomic_ids = [
        opportunity.deduction_id for opportunity in opportunities if opportunity.deduction_id.startswith("auto_")
    ]
    assert autonomic_ids == []


def test_gasto_deducible_rules_do_not_appear() -> None:
    """Cuotas sindicales / colegios son gasto_deducible: la app asume que
    están pre-descontadas en taxable_base.general, así que no se incluyen
    como oportunidades."""
    deductions = load_deductions()
    profile = _profile()
    evaluations = evaluate_deductions(deductions, profile)
    opportunities = detect_opportunities(profile, deductions, evaluations)
    ids = [opportunity.deduction_id for opportunity in opportunities]
    assert "es_cuotas_sindicales_2025" not in ids
    assert "es_cuotas_colegios_profesionales_2025" not in ids


def test_profile_with_all_data_returns_no_opportunities() -> None:
    """Si el perfil ya tiene todos los campos relevantes (o las reglas no
    aplican), no hay missing_data y por tanto no hay oportunidades nuevas."""
    deductions = load_deductions()
    profile = _profile(
        personal={
            "age": 35,
            "professional_association_required": False,
            "is_eligible_maternity_deduction": False,
            "large_family_category": "",
            "donations_recurrent_qualifying": False,
        },
        family={
            "spouse": {"net_work_and_economic_income": 30000.0},
            "maternity_qualifying_child_months": 0,
            "large_family_qualifying_months": 0,
            "disabled_descendants_qualifying_months": 0,
            "disabled_ascendants_qualifying_months": 0,
        },
        expenses={
            "union_dues_amount": 0,
            "professional_association_fees_amount": 0,
            "pension_plan_contribution_amount": 0,
            "spouse_pension_plan_contribution_amount": 0,
            "donations_amount": 0,
        },
    )
    evaluations = evaluate_deductions(deductions, profile)
    opportunities = detect_opportunities(profile, deductions, evaluations)
    # Con valores no positivos los requirements de tipo `> 0` fallan, las
    # reglas pasan a does_not_apply en lugar de missing_data; no entran en
    # sugerencias.
    assert opportunities == []


def test_maternity_opportunity_describes_required_documents() -> None:
    """La rationale debe mencionar el nombre de la deducción."""
    deductions = load_deductions()
    profile = _profile()
    evaluations = evaluate_deductions(deductions, profile)
    opportunities = detect_opportunities(profile, deductions, evaluations)
    maternity = next(opportunity for opportunity in opportunities if opportunity.deduction_id == "es_maternidad_2025")
    assert "maternidad" in maternity.rationale.lower()
    assert maternity.potential_savings_estimate > 0
