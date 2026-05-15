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
    AUTONOMIC_GENERAL_TARIFFS,
    AUTONOMIC_SAVINGS_TARIFF_2025,
    GENERAL_TARIFF_2025,
    GENERIC_AUTONOMIC_GENERAL_TARIFF_2025,
    SAVINGS_TARIFF_2025,
    STATE_GENERAL_TARIFF_2025,
    STATE_SAVINGS_TARIFF_2025,
    AutonomicTariffSet,
    TaxBracket,
    TaxScale,
    apply_scale,
    autonomic_general_tariff_for,
    compute_personal_family_minimum,
    compute_tax_comparison,
    compute_tax_summary,
)


def _profile(**overrides: Any) -> TaxProfile:
    data: dict[str, Any] = {
        "tax_year": 2025,
        # Asturias: no está en el registry de tarifas autonómicas, así que
        # se usa la genérica (= estatal) y las cuotas numéricas exactas
        # de estos tests siguen siendo válidas. Cuando un test necesite
        # verificar Madrid u otra CCAA registrada, pasa region como override.
        "region": "Asturias",
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
    # 350.000 €: hasta 300.000 acumulan; 50.000 * 0,49 = 24.500 en el tope.
    # Tarifa total = 2x estatal: el tramo final genérico es 49 % bajo la
    # asumción "autonómica = estatal".
    expected = 12450 * 0.19 + 7750 * 0.24 + 15000 * 0.30 + 24800 * 0.37 + 240000 * 0.45 + 50000 * 0.49
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


def test_minimum_disabled_descendant_33_64() -> None:
    profile = _profile(family={"disabled_descendants_33_64_count": 1})
    assert compute_personal_family_minimum(profile) == 5550.0 + 3000.0


def test_minimum_disabled_descendant_65_plus_uses_higher_bracket() -> None:
    profile = _profile(family={"disabled_descendants_65_plus_count": 1})
    assert compute_personal_family_minimum(profile) == 5550.0 + 9000.0


def test_minimum_disabled_descendant_with_assistance_adds_bonus() -> None:
    profile = _profile(
        family={
            "disabled_descendants_33_64_count": 1,
            "disabled_descendants_assistance_count": 1,
        }
    )
    # 3000 (33-64) + 3000 (asistencia) = 6000 extra
    assert compute_personal_family_minimum(profile) == 5550.0 + 3000.0 + 3000.0


def test_minimum_disabled_ascendants_same_amounts() -> None:
    profile = _profile(
        family={
            "disabled_ascendants_65_plus_count": 1,
            "disabled_ascendants_assistance_count": 1,
        }
    )
    # 9000 (≥65) + 3000 (asistencia) = 12000 extra
    assert compute_personal_family_minimum(profile) == 5550.0 + 9000.0 + 3000.0


def test_minimum_combines_descendants_and_ascendants_disabilities() -> None:
    profile = _profile(
        family={
            "disabled_descendants_33_64_count": 2,
            "disabled_ascendants_65_plus_count": 1,
        }
    )
    # 2*3000 (descendientes) + 1*9000 (ascendiente) = 15000 extra
    assert compute_personal_family_minimum(profile) == 5550.0 + 6000.0 + 9000.0


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


# ---------- compute_tax_comparison ----------


def test_comparison_returns_zero_savings_when_no_rules_apply() -> None:
    profile = _profile()
    comparison = compute_tax_comparison(profile, deductions=[], evaluations=[])
    assert comparison.ahorro_real == 0.0
    assert comparison.savings_per_rule == ()
    assert comparison.with_rules.cuota_diferencial == comparison.without_rules.cuota_diferencial


def test_comparison_for_realistic_profile_matches_manual_calculation() -> None:
    """Perfil con plan de pensiones + donativos. Verificamos el ahorro real
    exacto (1.108 € - 438 € = 670 €) y el detalle marginal por regla."""
    deductions = {d.id: d for d in load_deductions()}
    rules = [
        replace(
            deductions["es_aportaciones_plan_pensiones_individual_2025"],
            validation_status=ValidationStatus.VALIDADA,
        ),
        replace(
            deductions["es_donativos_no_recurrente_2025"],
            validation_status=ValidationStatus.VALIDADA,
        ),
    ]
    profile = _profile(
        personal={"age": 35},
        family={"children_count": 1, "children_under_3_count": 1},
        income={"work_income": 35000.0},
        withholdings=[{"amount": 5800.0}],
        taxable_base={
            "general": 35000.0,
            "savings": 1500.0,
            "liquidable": 33000.0,
            "net_work_and_economic_income": 32000.0,
        },
        expenses={
            "pension_plan_contribution_amount": 2000.0,
            "donations_amount": 300.0,
        },
        documents=[
            "Certificado de aportación al plan de pensiones",
            "Certificado de donativo expedido por la entidad beneficiaria",
        ],
    )
    evaluations = evaluate_deductions(rules, profile)
    comparison = compute_tax_comparison(profile, rules, evaluations)

    assert abs(comparison.with_rules.cuota_diferencial - 438.0) < 0.01
    assert abs(comparison.without_rules.cuota_diferencial - 1108.0) < 0.01
    assert abs(comparison.ahorro_real - 670.0) < 0.01

    savings_by_id = {item.deduction_id: item.ahorro_marginal for item in comparison.savings_per_rule}
    assert "es_aportaciones_plan_pensiones_individual_2025" in savings_by_id
    assert "es_donativos_no_recurrente_2025" in savings_by_id
    # Plan de pensiones (reducción de 1500 al tipo marginal del 30%): 450 €
    assert abs(savings_by_id["es_aportaciones_plan_pensiones_individual_2025"] - 450.0) < 0.01
    # Donativo (deducción de cuota directa): 220 €
    assert abs(savings_by_id["es_donativos_no_recurrente_2025"] - 220.0) < 0.01


def test_comparison_gasto_deducible_has_zero_marginal_savings() -> None:
    """Las cuotas sindicales (gasto_deducible) aplican en evaluate pero
    NO afectan el cómputo de cuota: marginal 0 €."""
    deductions = {d.id: d for d in load_deductions()}
    rules = [deductions["es_cuotas_sindicales_2025"]]  # ya validada en corpus
    profile = _profile(
        income={"work_income": 30000.0},
        expenses={"union_dues_amount": 220.0},
        documents=["Justificante de pago de cuotas sindicales"],
    )
    evaluations = evaluate_deductions(rules, profile)
    comparison = compute_tax_comparison(profile, rules, evaluations)
    assert comparison.ahorro_real == 0.0
    saving = next(s for s in comparison.savings_per_rule if s.deduction_id == "es_cuotas_sindicales_2025")
    assert saving.ahorro_marginal == 0.0


def test_comparison_with_bonification_attributes_full_savings() -> None:
    """Una bonificación de cuota se traduce íntegra en ahorro real."""
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
    comparison = compute_tax_comparison(profile, [ceuta], evaluations)
    # Bonificación = 3000 * 0.60 = 1800 (sale del cálculo de cuota).
    # La cuota líquida no puede ir negativa: si la cuota integra < 1800,
    # el ahorro real = cuota integra. Si > 1800, ahorro real = 1800.
    assert comparison.ahorro_real > 0
    assert comparison.ahorro_real <= 1800.01


# ---------- Split estatal/autonómica de la tarifa ----------


def test_state_and_generic_autonomic_tariffs_sum_to_total_tariff() -> None:
    """Para cualquier base, la suma de la cuota estatal + la genérica autonómica
    debe coincidir con la tarifa total (equivalencia matemática del refactor)."""
    for base in (5_000.0, 12_450.0, 30_000.0, 80_000.0, 500_000.0):
        state = apply_scale(base, STATE_GENERAL_TARIFF_2025)
        autonomic = apply_scale(base, GENERIC_AUTONOMIC_GENERAL_TARIFF_2025)
        total = apply_scale(base, GENERAL_TARIFF_2025)
        assert abs(state + autonomic - total) < 0.01, f"base={base}"


def test_state_and_autonomic_savings_tariffs_sum_to_total() -> None:
    for base in (1_000.0, 6_000.0, 30_000.0, 300_000.0):
        state = apply_scale(base, STATE_SAVINGS_TARIFF_2025)
        autonomic = apply_scale(base, AUTONOMIC_SAVINGS_TARIFF_2025)
        total = apply_scale(base, SAVINGS_TARIFF_2025)
        assert abs(state + autonomic - total) < 0.01, f"base={base}"


def test_autonomic_general_tariff_for_unknown_region_returns_generic() -> None:
    assert autonomic_general_tariff_for(None) is GENERIC_AUTONOMIC_GENERAL_TARIFF_2025
    assert autonomic_general_tariff_for("Asturias") is GENERIC_AUTONOMIC_GENERAL_TARIFF_2025
    assert autonomic_general_tariff_for("Region inventada") is GENERIC_AUTONOMIC_GENERAL_TARIFF_2025


def test_autonomic_registry_contains_madrid() -> None:
    """El registry productivo incluye la tarifa autonómica de Madrid
    (cifras del ejercicio 2024, pendientes de verificar para 2025)."""
    assert "Madrid" in AUTONOMIC_GENERAL_TARIFFS
    madrid = AUTONOMIC_GENERAL_TARIFFS["Madrid"].general
    # El nombre lleva el marcador de pendiente de verificación.
    assert "pendiente_verificacion" in madrid.name
    # Sanity check: 5 tramos, tope final 20,5 %.
    assert len(madrid.brackets) == 5
    assert madrid.brackets[-1].rate == 0.205


def test_madrid_tariff_is_lower_than_generic_for_typical_base() -> None:
    """Madrid 2024 tiene tipos autonómicos menores que la genérica
    (= estatal). Para una base de 30.000 € la cuota autonómica Madrid
    debe ser inferior a la genérica."""
    base = 30000.0
    cuota_madrid = apply_scale(base, autonomic_general_tariff_for("Madrid"))
    cuota_generica = apply_scale(base, GENERIC_AUTONOMIC_GENERAL_TARIFF_2025)
    assert cuota_madrid < cuota_generica
    # Diferencia esperada del orden de cientos de euros.
    assert (cuota_generica - cuota_madrid) > 100.0


def test_overriding_a_region_changes_the_tax_summary() -> None:
    """Smoke test: si registramos una tarifa autonómica distinta para una
    región concreta, la cuota integra cambia para perfiles de esa región."""
    fake_region = "Region_de_prueba"
    higher_tariff = TaxScale(
        name="test_autonomica_alta",
        brackets=(
            TaxBracket(up_to=12_450.0, rate=0.20),  # vs 0.095 genérica
            TaxBracket(up_to=None, rate=0.30),  # vs 0.245 final genérica
        ),
    )
    AUTONOMIC_GENERAL_TARIFFS[fake_region] = AutonomicTariffSet(general=higher_tariff)
    try:
        profile = _profile(region=fake_region)
        summary = compute_tax_summary(profile, deductions=[], evaluations=[])
        baseline = compute_tax_summary(_profile(region="Madrid"), deductions=[], evaluations=[])
        # La tarifa de prueba es más alta → cuota integra mayor que la genérica.
        assert summary.cuota_integra_general > baseline.cuota_integra_general
    finally:
        del AUTONOMIC_GENERAL_TARIFFS[fake_region]


def test_autonomic_tariff_matches_case_insensitively() -> None:
    fake_region = "Region_Insensitive_Case"
    higher_tariff = TaxScale(
        name="test_case_insensitive",
        brackets=(TaxBracket(up_to=None, rate=0.50),),
    )
    AUTONOMIC_GENERAL_TARIFFS[fake_region] = AutonomicTariffSet(general=higher_tariff)
    try:
        assert autonomic_general_tariff_for("region_insensitive_case") is higher_tariff
        assert autonomic_general_tariff_for("REGION_INSENSITIVE_CASE") is higher_tariff
    finally:
        del AUTONOMIC_GENERAL_TARIFFS[fake_region]


def test_madrid_profile_produces_lower_cuota_than_unregistered_region() -> None:
    """Con Madrid en el registry productivo, un perfil de Madrid produce
    cuota integra menor que un perfil de una CCAA sin tarifa específica
    (Asturias, que usa la genérica = estatal)."""
    madrid_summary = compute_tax_summary(_profile(region="Madrid"), deductions=[], evaluations=[])
    generic_summary = compute_tax_summary(_profile(region="Asturias"), deductions=[], evaluations=[])
    assert madrid_summary.cuota_integra_general < generic_summary.cuota_integra_general
    assert madrid_summary.cuota_diferencial < generic_summary.cuota_diferencial


def test_madrid_cuota_integra_general_matches_manual_calculation_for_30k() -> None:
    """Verificación numérica del cómputo Madrid para base 30.000 € y mínimo
    5.550 € (perfil sin hijos ni discapacidad). Cálculo manual:

      Estatal sobre 30.000:    12450*0.095 + 7750*0.12 + 9800*0.15 = 3.582,75
      Estatal sobre 5.550:     5550 * 0.095 = 527,25
      Cuota estatal neta:      3.055,50

      Madrid sobre 30.000:     13362.22*0.085 + 5642.41*0.107 + 10995.37*0.128
                               = 1.135,7887 + 603,7378 + 1.407,4074 = 3.146,9339
      Madrid sobre 5.550:      5550 * 0.085 = 471,75
      Cuota Madrid neta:       2.675,1839

      Cuota integra general:   3.055,50 + 2.675,1839 = 5.730,6839
    """
    summary = compute_tax_summary(_profile(region="Madrid"), deductions=[], evaluations=[])
    expected = (12450 * 0.095 + 7750 * 0.12 + 9800 * 0.15 - 5550 * 0.095) + (
        13362.22 * 0.085 + (19004.63 - 13362.22) * 0.107 + (30000 - 19004.63) * 0.128 - 5550 * 0.085
    )
    assert abs(summary.cuota_integra_general - expected) < 0.01
