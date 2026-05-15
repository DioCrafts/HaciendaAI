"""Tests numéricos del cálculo IRPF (cuota líquida diferencial).

Cubre la tarifa progresiva, el cálculo del mínimo personal y familiar y
el cómputo de la cuota líquida con reducciones, deducciones y
bonificaciones aplicadas según la categoría declarada en el corpus.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import TaxProfile, ValidationStatus
from hacienda_ai.rules import evaluate_deductions
from hacienda_ai.tax_calculation import (
    GENERAL_TARIFF_2025,
    SAVINGS_TARIFF_2025,
    apply_scale,
    compute_personal_family_minimum,
    compute_tax_summary,
)


def _profile(**overrides: Any) -> TaxProfile:
    data: dict[str, Any] = {
        "tax_year": 2025,
        "region": "Madrid",
        "personal": {"age": 30},
        "income": {"work_income": 30000.0},
        "withholdings": [{"amount": 4000.0}],
        "taxable_base": {
            "general": 30000.0,
            "savings": 0.0,
            "liquidable": 30000.0,
            "net_work_and_economic_income": 30000.0,
        },
    }
    data.update(overrides)
    return TaxProfile.from_dict(data)


# ---------- apply_scale ----------


def test_apply_scale_zero_returns_zero() -> None:
    assert apply_scale(0.0, GENERAL_TARIFF_2025) == 0.0


def test_apply_scale_within_first_bracket() -> None:
    # 5.000 € * 19 % = 950 €
    assert apply_scale(5000.0, GENERAL_TARIFF_2025) == 950.0


def test_apply_scale_at_first_bracket_top() -> None:
    # 12.450 € * 19 % = 2.365,50 €
    assert apply_scale(12450.0, GENERAL_TARIFF_2025) == 2365.5


def test_apply_scale_spans_three_brackets() -> None:
    # 30.000 €: 12.450*19% + 7.750*24% + 9.800*30% = 2.365,5 + 1.860 + 2.940 = 7.165,5
    assert apply_scale(30000.0, GENERAL_TARIFF_2025) == 7165.5


def test_apply_scale_above_top_bracket() -> None:
    # 350.000 €: hasta 300.000 acumulan 92.376; 50.000 * 0,47 = 23.500; total 115.876
    # Suma exacta: 12450*0.19 + 7750*0.24 + 15000*0.30 + 24800*0.37 + 240000*0.45 + 50000*0.47
    expected = 12450 * 0.19 + 7750 * 0.24 + 15000 * 0.30 + 24800 * 0.37 + 240000 * 0.45 + 50000 * 0.47
    assert apply_scale(350000.0, GENERAL_TARIFF_2025) == expected


def test_apply_savings_scale_first_bracket() -> None:
    assert apply_scale(5000.0, SAVINGS_TARIFF_2025) == 950.0


def test_apply_savings_scale_two_brackets() -> None:
    # 10.000 €: 6.000*19% + 4.000*21% = 1.140 + 840 = 1.980
    assert apply_scale(10000.0, SAVINGS_TARIFF_2025) == 1980.0


# ---------- compute_personal_family_minimum ----------


def test_minimum_personal_base_only() -> None:
    profile = _profile(personal={"age": 30})
    assert compute_personal_family_minimum(profile) == 5550.0


def test_minimum_personal_age_65_bonus() -> None:
    profile = _profile(personal={"age": 67})
    assert compute_personal_family_minimum(profile) == 5550.0 + 1150.0


def test_minimum_personal_age_75_adds_both_bonuses() -> None:
    profile = _profile(personal={"age": 80})
    assert compute_personal_family_minimum(profile) == 5550.0 + 1150.0 + 1400.0


def test_minimum_with_two_children() -> None:
    profile = _profile(family={"children_count": 2})
    # Base 5550 + 1.º hijo 2400 + 2.º hijo 2700 = 10.650
    assert compute_personal_family_minimum(profile) == 10650.0


def test_minimum_with_four_children_uses_top_bracket_for_fourth() -> None:
    profile = _profile(family={"children_count": 4})
    # 5550 + 2400 + 2700 + 4000 + 4500 = 19.150
    assert compute_personal_family_minimum(profile) == 19150.0


def test_minimum_with_children_under_3_adds_bonus() -> None:
    profile = _profile(family={"children_count": 1, "children_under_3_count": 1})
    assert compute_personal_family_minimum(profile) == 5550.0 + 2400.0 + 2800.0


def test_minimum_ascendants() -> None:
    profile = _profile(family={"ascendants_qualifying_count": 2, "ascendants_over_75_count": 1})
    assert compute_personal_family_minimum(profile) == 5550.0 + 2 * 1150.0 + 1400.0


def test_minimum_disability_33() -> None:
    profile = _profile(personal={"age": 30, "disability_percentage": 40})
    assert compute_personal_family_minimum(profile) == 5550.0 + 3000.0


def test_minimum_disability_65_with_third_person_help() -> None:
    profile = _profile(personal={"age": 30, "disability_percentage": 70, "needs_third_person_help": True})
    assert compute_personal_family_minimum(profile) == 5550.0 + 9000.0 + 3000.0


def test_minimum_override_takes_precedence() -> None:
    profile = _profile(family={"personal_family_minimum_override": 12345.0})
    assert compute_personal_family_minimum(profile) == 12345.0


# ---------- compute_tax_summary: doble escala y categorías ----------


def test_tax_summary_with_no_rules_applies_minimum_correctly() -> None:
    profile = _profile()
    summary = compute_tax_summary(profile, deductions=[], evaluations=[])
    # Base 30.000 - mínimo 5.550 — doble escala:
    # cuota_general(30000) = 7.165,5
    # cuota_general(5550) = 5550 * 0.19 = 1.054,5
    # cuota integra = 6.111
    assert summary.cuota_integra_general == 7165.5 - 1054.5
    assert summary.cuota_integra_ahorro == 0.0
    assert summary.cuota_correspondiente_al_minimo == 1054.5


def test_tax_summary_subtracts_withholdings_for_cuota_diferencial() -> None:
    profile = _profile(withholdings=[{"amount": 4000.0}])
    summary = compute_tax_summary(profile, deductions=[], evaluations=[])
    # cuota líquida = 6.111; retenciones 4.000 → diferencial = 2.111
    assert summary.cuota_diferencial == summary.cuota_liquida - 4000.0


def test_tax_summary_applies_reduction_to_base_liquidable() -> None:
    """Una reducción del corpus reduce la base liquidable general, NO se
    resta de la cuota."""
    deductions = {d.id: d for d in load_deductions()}
    pension = replace(
        deductions["es_aportaciones_plan_pensiones_individual_2025"],
        validation_status=ValidationStatus.VALIDADA,
    )
    profile = _profile(
        expenses={"pension_plan_contribution_amount": 1500.0},
        documents=["Certificado de aportación al plan de pensiones"],
    )
    evaluations = evaluate_deductions([pension], profile)
    summary = compute_tax_summary(profile, deductions=[pension], evaluations=evaluations)
    # Base general 30.000 - reducción 1.500 = 28.500 base liquidable
    assert summary.reducciones_aplicadas == 1500.0
    assert summary.base_liquidable_general == 28500.0
    assert "es_aportaciones_plan_pensiones_individual_2025" in summary.applied_reduction_ids


def test_tax_summary_applies_cuota_deduction_after_tariff() -> None:
    """Una deducción de cuota (donativos) reduce la cuota líquida
    directamente — no la base."""
    deductions = {d.id: d for d in load_deductions()}
    donativos = replace(
        deductions["es_donativos_no_recurrente_2025"],
        validation_status=ValidationStatus.VALIDADA,
    )
    profile = _profile(
        expenses={"donations_amount": 250.0},
        taxable_base={
            "general": 30000.0,
            "savings": 0.0,
            "liquidable": 30000.0,
            "net_work_and_economic_income": 30000.0,
        },
        documents=["Certificado de donativo expedido por la entidad beneficiaria"],
    )
    evaluations = evaluate_deductions([donativos], profile)
    summary = compute_tax_summary(profile, deductions=[donativos], evaluations=evaluations)
    # Donativo 250 * 0.80 = 200 que se resta de la cuota líquida
    assert summary.deducciones_de_cuota == 200.0
    assert summary.reducciones_aplicadas == 0.0
    assert "es_donativos_no_recurrente_2025" in summary.applied_cuota_deduction_ids


def test_tax_summary_separates_bonifications_from_other_cuota_deductions() -> None:
    """Las bonificaciones (cuota_bonification, ej. Ceuta/Melilla) van a
    su propio cubo aunque reduzcan la cuota igual que las demás."""
    deductions = {d.id: d for d in load_deductions()}
    ceuta = replace(
        deductions["es_bonificacion_ceuta_2025"],
        validation_status=ValidationStatus.VALIDADA,
    )
    profile = _profile(
        region="Ceuta",
        cuota={"attributable_to_ceuta_melilla": 3000.0},
        documents=["Certificado de residencia o documentación que acredite la obtención de rentas en Ceuta"],
    )
    evaluations = evaluate_deductions([ceuta], profile)
    summary = compute_tax_summary(profile, deductions=[ceuta], evaluations=evaluations)
    assert summary.bonificaciones_cuota == 1800.0  # 3000 * 0.60
    assert summary.deducciones_de_cuota == 0.0


def test_tax_summary_minimum_remainder_reduces_savings_base() -> None:
    """Si la base general es menor que el mínimo, el remanente reduce la
    base del ahorro (art. 56.2 segundo párrafo LIRPF)."""
    profile = _profile(
        taxable_base={"general": 3000.0, "savings": 10000.0},
        family={"children_count": 1},  # mínimo = 5550 + 2400 = 7950
    )
    summary = compute_tax_summary(profile, deductions=[], evaluations=[])
    # Mínimo 7.950: absorbe 3.000 de la general (deja cuota general en 0).
    # Resto 4.950 reduce la base del ahorro.
    # Cuota ahorro(10.000) = 6000*0.19 + 4000*0.21 = 1.140 + 840 = 1.980
    # Cuota ahorro correspondiente al mínimo absorbido (4.950) = 4950*0.19 = 940.5
    # Cuota integra ahorro = 1.980 - 940,50 = 1.039,50
    assert summary.cuota_integra_general == 0.0
    assert abs(summary.cuota_integra_ahorro - 1039.5) < 0.01


def test_tax_summary_cuota_liquida_never_negative() -> None:
    """Si las deducciones superan la cuota íntegra, la cuota líquida
    queda en 0 (no negativa). La cuota diferencial sí puede ser negativa
    al restar retenciones."""
    profile = _profile(
        taxable_base={"general": 8000.0, "savings": 0.0},  # cuota integra muy baja
        withholdings=[{"amount": 5000.0}],
    )
    summary = compute_tax_summary(profile, deductions=[], evaluations=[])
    assert summary.cuota_liquida >= 0.0
    # Devolución: cuota_diferencial negativa
    assert summary.cuota_diferencial < 0.0


def test_tax_summary_ignores_gasto_deducible_to_avoid_double_counting() -> None:
    """Cuotas sindicales son gasto_deducible: el motor las evalúa pero
    NO se restan en el cómputo de cuota (asumimos pre-descontadas)."""
    deductions = {d.id: d for d in load_deductions()}
    sindicales = deductions["es_cuotas_sindicales_2025"]  # ya validada en el corpus
    profile = _profile(
        income={"work_income": 30000.0},
        expenses={"union_dues_amount": 220.0},
        documents=["Justificante de pago de cuotas sindicales"],
    )
    evaluations = evaluate_deductions([sindicales], profile)
    summary = compute_tax_summary(profile, deductions=[sindicales], evaluations=evaluations)
    # Aunque la regla aplica con importe 220 €, no se resta ni de la base
    # liquidable ni de la cuota líquida en este cálculo.
    assert summary.reducciones_aplicadas == 0.0
    assert summary.deducciones_de_cuota == 0.0
    assert summary.applied_reduction_ids == ()
    assert summary.applied_cuota_deduction_ids == ()
