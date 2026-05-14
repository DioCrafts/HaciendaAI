from __future__ import annotations

import pytest

from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import Deduction, TaxProfile, ValidationError
from hacienda_ai.rules import evaluate_deduction, evaluate_deductions
from hacienda_ai.safety import screen_user_request


def validated_deduction(**overrides):
    data = {
        "id": "test_validada",
        "name": "Deducción validada de prueba",
        "description": "Regla sintética usada solo para probar el motor determinista.",
        "tax_year": 2025,
        "scope": "estatal",
        "region": None,
        "category": "deduccion",
        "requirements": [{"field": "expenses.test_amount", "operator": ">", "value": 0}],
        "calculation": {"type": "amount_field", "base_field": "expenses.test_amount"},
        "limit": 100.0,
        "taxable_base_limits": {},
        "incompatibilities": [],
        "required_documents": ["Justificante de prueba"],
        "rent_web_boxes": [],
        "sources": [{"type": "test", "title": "Fuente sintética de test", "url": None, "checked_at": "2026-05-11"}],
        "effective_from": "2025-01-01",
        "effective_to": "2025-12-31",
        "last_reviewed_at": "2026-05-11",
        "risk_level": "bajo",
        "validation_status": "validada",
    }
    data.update(overrides)
    return Deduction.from_dict(data)


def profile(**overrides):
    data = {
        "tax_year": 2025,
        "region": "Madrid",
        "expenses": {"test_amount": 120.0},
        "documents": ["Justificante de prueba"],
    }
    data.update(overrides)
    return TaxProfile.from_dict(data)


def test_loads_state_corpus_with_normalized_schema():
    deductions = load_deductions()
    assert {deduction.id for deduction in deductions} >= {
        "es_cuotas_sindicales_2025",
        "es_cuotas_colegios_profesionales_2025",
        "es_aportaciones_plan_pensiones_individual_2025",
        "es_aportaciones_plan_pensiones_conyuge_2025",
        "es_donativos_no_recurrente_2025",
        "es_donativos_recurrente_2025",
        "es_maternidad_2025",
        "es_familia_numerosa_general_2025",
        "es_familia_numerosa_especial_2025",
        "es_descendiente_discapacidad_2025",
        "es_ascendiente_discapacidad_2025",
    }
    assert all(deduction.sources for deduction in deductions)


def test_rejects_deduction_without_source():
    with pytest.raises(ValidationError, match="al menos una fuente"):
        validated_deduction(sources=[])


def test_rejects_unsupported_operator():
    with pytest.raises(ValidationError, match="Operador"):
        validated_deduction(requirements=[{"field": "x", "operator": "contains", "value": 1}])


def test_pending_validation_deduction_is_not_recommended_directly():
    deduction = load_deductions()[0]
    assert deduction.validation_status.value != "validada"
    result = evaluate_deduction(
        deduction,
        profile(income={"work_income": 25000.0}, expenses={"union_dues_amount": 50.0}),
    )
    assert result.status == "pending_validation"
    assert result.estimated_amount == 0.0


def test_validated_deduction_applies_with_evidence_and_caps_amount():
    result = evaluate_deduction(validated_deduction(), profile())
    assert result.status == "applies"
    assert result.estimated_amount == 100.0
    assert result.risk_level == "low"


def test_validated_deduction_detects_missing_data():
    result = evaluate_deduction(validated_deduction(), profile(expenses={}))
    assert result.status == "missing_data"
    assert result.missing_fields == ("expenses.test_amount",)


def test_validated_deduction_detects_missing_evidence():
    result = evaluate_deduction(validated_deduction(), profile(documents=[]))
    assert result.status == "missing_evidence"
    assert result.missing_documents == ("Justificante de prueba",)


def test_validated_deduction_does_not_apply_when_requirement_fails():
    result = evaluate_deduction(validated_deduction(), profile(expenses={"test_amount": 0.0}))
    assert result.status == "does_not_apply"


def test_deduction_for_other_tax_year_does_not_apply():
    result = evaluate_deduction(validated_deduction(tax_year=2024), profile())
    assert result.status == "does_not_apply"


def test_deduction_for_other_region_does_not_apply():
    result = evaluate_deduction(validated_deduction(scope="autonomico", region="Cataluña"), profile(region="Madrid"))
    assert result.status == "does_not_apply"


def test_safety_rejects_false_invoice_request():
    allowed, message = screen_user_request("Quiero meter facturas falsas para pagar menos")
    assert allowed is False
    assert "No puedo ayudar" in message
    assert "legalidad" in message


def test_safety_allows_legal_optimization_request():
    allowed, message = screen_user_request("Quiero revisar deducciones legales y documentación necesaria")
    assert allowed is True
    assert message is None


def test_exists_operator_treats_missing_path_as_unmet_requirement():
    deduction = validated_deduction(
        requirements=[{"field": "personal.disability_certificate", "operator": "exists"}],
        calculation={"type": "fixed_amount", "fixed_amount": 50.0},
    )
    result = evaluate_deduction(deduction, profile())
    assert result.status == "does_not_apply"
    assert result.missing_fields == ()


def test_not_exists_operator_passes_when_path_is_missing():
    deduction = validated_deduction(
        requirements=[{"field": "personal.disability_certificate", "operator": "not_exists"}],
        calculation={"type": "fixed_amount", "fixed_amount": 50.0},
    )
    result = evaluate_deduction(deduction, profile())
    assert result.status == "applies"
    assert result.estimated_amount == 50.0


def test_tax_year_rejects_boolean_value():
    with pytest.raises(ValidationError, match="tax_year"):
        validated_deduction(tax_year=True)


def test_tax_profile_tax_year_rejects_boolean_value():
    with pytest.raises(ValidationError, match=r"tax_profile\.tax_year"):
        TaxProfile.from_dict({"tax_year": True, "region": "Madrid"})


def test_deduction_accepts_null_for_optional_list_fields():
    deduction = validated_deduction(
        incompatibilities=None,
        rent_web_boxes=None,
    )
    assert deduction.incompatibilities == ()
    assert deduction.rent_web_boxes == ()


def test_deduction_accepts_null_required_documents():
    deduction = validated_deduction(required_documents=None)
    assert deduction.required_documents == ()


def test_autonomic_deduction_requires_region():
    with pytest.raises(ValidationError, match="scope='autonomico'"):
        validated_deduction(scope="autonomico", region=None)


def test_effective_to_before_tax_year_marks_deduction_as_not_applying():
    deduction = validated_deduction(effective_from="2024-01-01", effective_to="2024-12-31", tax_year=2024)
    result = evaluate_deduction(deduction, profile(tax_year=2024))
    assert result.status == "applies"
    expired = validated_deduction(effective_from="2023-01-01", effective_to="2024-12-31")
    result = evaluate_deduction(expired, profile())
    assert result.status == "does_not_apply"
    assert "vigor" in result.reason


def test_effective_from_after_tax_year_marks_deduction_as_not_applying():
    deduction = validated_deduction(effective_from="2026-01-01", effective_to="2026-12-31")
    result = evaluate_deduction(deduction, profile())
    assert result.status == "does_not_apply"
    assert "vigor" in result.reason


def test_invalid_effective_date_is_rejected_at_parse_time():
    with pytest.raises(ValidationError, match="ISO"):
        validated_deduction(effective_from="hoy")


def test_effective_from_after_effective_to_is_rejected():
    with pytest.raises(ValidationError, match="effective_from"):
        validated_deduction(effective_from="2025-12-31", effective_to="2025-01-01")


def test_incompatibilities_keep_only_highest_estimated_amount():
    bigger = validated_deduction(
        id="big",
        incompatibilities=["small"],
    )
    smaller = validated_deduction(
        id="small",
        incompatibilities=["big"],
        calculation={"type": "fixed_amount", "fixed_amount": 10.0},
    )
    results = evaluate_deductions([bigger, smaller], profile())
    by_id = {r.deduction_id: r for r in results}
    assert by_id["big"].status == "applies"
    assert by_id["big"].estimated_amount == 100.0
    assert by_id["small"].status == "does_not_apply"
    assert by_id["small"].estimated_amount == 0.0
    assert "Incompatible con la deducción big" in by_id["small"].reason


def test_incompatibility_relation_is_symmetric_when_declared_one_way():
    bigger = validated_deduction(id="big", incompatibilities=[])
    smaller = validated_deduction(
        id="small",
        incompatibilities=["big"],
        calculation={"type": "fixed_amount", "fixed_amount": 10.0},
    )
    results = evaluate_deductions([bigger, smaller], profile())
    by_id = {r.deduction_id: r for r in results}
    assert by_id["big"].status == "applies"
    assert by_id["small"].status == "does_not_apply"


def test_incompatibility_ignored_when_only_one_side_applies():
    applies = validated_deduction(id="applies_ok", incompatibilities=["missing"])
    missing = validated_deduction(
        id="missing",
        incompatibilities=["applies_ok"],
        requirements=[{"field": "expenses.nonexistent", "operator": ">", "value": 0}],
    )
    results = evaluate_deductions([applies, missing], profile())
    by_id = {r.deduction_id: r for r in results}
    assert by_id["applies_ok"].status == "applies"
    assert by_id["missing"].status == "missing_data"


# ---------- tiered_percentage ----------


def _tiered_deduction(**overrides):
    defaults = dict(
        id="tiered_test",
        calculation={
            "type": "tiered_percentage",
            "base_field": "expenses.tiered_base",
            "tiers": [
                {"up_to": 250, "percentage": 0.80},
                {"up_to": None, "percentage": 0.40},
            ],
        },
        requirements=[{"field": "expenses.tiered_base", "operator": ">", "value": 0}],
        limit=None,
    )
    defaults.update(overrides)
    return validated_deduction(**defaults)


def test_tiered_percentage_applies_only_first_tier_when_below_threshold():
    result = evaluate_deduction(_tiered_deduction(), profile(expenses={"tiered_base": 100.0}))
    assert result.status == "applies"
    assert result.estimated_amount == 80.0  # 100 * 0.80


def test_tiered_percentage_at_threshold_uses_first_tier_only():
    result = evaluate_deduction(_tiered_deduction(), profile(expenses={"tiered_base": 250.0}))
    assert result.estimated_amount == 200.0  # 250 * 0.80


def test_tiered_percentage_splits_amount_across_tiers():
    result = evaluate_deduction(_tiered_deduction(), profile(expenses={"tiered_base": 300.0}))
    assert result.estimated_amount == 220.0  # 250 * 0.80 + 50 * 0.40


def test_tiered_percentage_handles_large_amounts():
    result = evaluate_deduction(_tiered_deduction(), profile(expenses={"tiered_base": 1000.0}))
    assert result.estimated_amount == 500.0  # 250 * 0.80 + 750 * 0.40


def test_tiered_percentage_respects_global_limit():
    deduction = _tiered_deduction(limit=150.0)
    result = evaluate_deduction(deduction, profile(expenses={"tiered_base": 1000.0}))
    assert result.estimated_amount == 150.0


def test_tiered_percentage_with_three_tiers():
    deduction = validated_deduction(
        id="tiered_three",
        calculation={
            "type": "tiered_percentage",
            "base_field": "expenses.tiered_base",
            "tiers": [
                {"up_to": 100, "percentage": 0.90},
                {"up_to": 300, "percentage": 0.50},
                {"up_to": None, "percentage": 0.20},
            ],
        },
        requirements=[{"field": "expenses.tiered_base", "operator": ">", "value": 0}],
        limit=None,
    )
    result = evaluate_deduction(deduction, profile(expenses={"tiered_base": 500.0}))
    # 100*0.90 + 200*0.50 + 200*0.20 = 90 + 100 + 40 = 230
    assert result.estimated_amount == 230.0


def test_tiered_percentage_rejects_empty_tiers():
    with pytest.raises(ValidationError, match="tiers es obligatorio"):
        validated_deduction(calculation={"type": "tiered_percentage", "base_field": "x"})


def test_tiered_percentage_rejects_non_ascending_thresholds():
    with pytest.raises(ValidationError, match="crecientes"):
        validated_deduction(
            calculation={
                "type": "tiered_percentage",
                "base_field": "x",
                "tiers": [
                    {"up_to": 300, "percentage": 0.50},
                    {"up_to": 200, "percentage": 0.20},
                ],
            },
        )


def test_tiered_percentage_rejects_unbounded_tier_in_the_middle():
    with pytest.raises(ValidationError, match="último tier"):
        validated_deduction(
            calculation={
                "type": "tiered_percentage",
                "base_field": "x",
                "tiers": [
                    {"up_to": None, "percentage": 0.50},
                    {"up_to": 500, "percentage": 0.20},
                ],
            },
        )


def test_tier_rejects_percentage_out_of_range():
    with pytest.raises(ValidationError, match="entre 0 y 1"):
        validated_deduction(
            calculation={
                "type": "tiered_percentage",
                "base_field": "x",
                "tiers": [{"up_to": 100, "percentage": 1.5}],
            },
        )


def test_taxable_base_limits_rejects_unknown_key():
    with pytest.raises(ValidationError, match="clave reconocida"):
        validated_deduction(taxable_base_limits={"max_percentage_of_unknown_base": 0.10})


def test_taxable_base_limits_rejects_value_out_of_range():
    with pytest.raises(ValidationError, match="entre 0 y 1"):
        validated_deduction(taxable_base_limits={"max_percentage_of_base_liquidable": 1.5})


def test_taxable_base_limits_rejects_non_numeric_value():
    with pytest.raises(ValidationError, match="entre 0 y 1"):
        validated_deduction(taxable_base_limits={"max_percentage_of_base_liquidable": True})


def test_taxable_base_limits_applies_minimum_of_multiple_caps():
    deduction = validated_deduction(
        calculation={"type": "fixed_amount", "fixed_amount": 10000.0},
        requirements=[],
        limit=None,
        taxable_base_limits={
            "max_percentage_of_base_liquidable": 0.10,
            "max_percentage_of_base_general": 0.05,
        },
    )
    result = evaluate_deduction(
        deduction,
        profile(taxable_base={"liquidable": 50_000.0, "general": 40_000.0}),
    )
    # cap_liquidable = 5.000; cap_general = 2.000; importe = 10.000 → min = 2.000
    assert result.status == "applies"
    assert result.estimated_amount == 2000.0


# ---------- prorated_fixed_amount ----------


def _prorated_deduction(**overrides):
    defaults = dict(
        id="prorated_test",
        calculation={
            "type": "prorated_fixed_amount",
            "monthly_amount": 100.0,
            "months_field": "family.qualifying_months",
            "months_cap": 12,
        },
        requirements=[{"field": "family.qualifying_months", "operator": ">", "value": 0}],
        limit=None,
    )
    defaults.update(overrides)
    return validated_deduction(**defaults)


def test_prorated_basic_computes_monthly_times_months():
    result = evaluate_deduction(_prorated_deduction(), profile(family={"qualifying_months": 6}))
    assert result.status == "applies"
    assert result.estimated_amount == 600.0


def test_prorated_caps_at_months_cap():
    result = evaluate_deduction(_prorated_deduction(), profile(family={"qualifying_months": 18}))
    assert result.estimated_amount == 1200.0  # 12 * 100


def test_prorated_without_months_cap_allows_more_than_12():
    deduction = _prorated_deduction(
        calculation={
            "type": "prorated_fixed_amount",
            "monthly_amount": 100.0,
            "months_field": "family.qualifying_months",
        },
    )
    result = evaluate_deduction(deduction, profile(family={"qualifying_months": 24}))
    assert result.estimated_amount == 2400.0


def test_prorated_respects_deduction_limit_after_prorrating():
    deduction = _prorated_deduction(limit=500.0)
    result = evaluate_deduction(deduction, profile(family={"qualifying_months": 10}))
    assert result.estimated_amount == 500.0  # 1000 capped at 500


def test_prorated_rejects_missing_monthly_amount():
    with pytest.raises(ValidationError, match="monthly_amount"):
        validated_deduction(
            calculation={"type": "prorated_fixed_amount", "months_field": "family.qualifying_months"},
        )


def test_prorated_rejects_missing_months_field():
    with pytest.raises(ValidationError, match="months_field"):
        validated_deduction(
            calculation={"type": "prorated_fixed_amount", "monthly_amount": 100.0},
        )


def test_other_calculation_types_reject_prorated_fields():
    with pytest.raises(ValidationError, match="monthly_amount solo se acepta"):
        validated_deduction(
            calculation={
                "type": "fixed_amount",
                "fixed_amount": 100.0,
                "monthly_amount": 50.0,
            },
        )


def test_calculation_rejects_tiers_for_non_tiered_type():
    with pytest.raises(ValidationError, match="tiers solo se acepta"):
        validated_deduction(
            calculation={
                "type": "amount_field",
                "base_field": "x",
                "tiers": [{"up_to": 100, "percentage": 0.50}],
            },
        )
