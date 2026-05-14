from __future__ import annotations

import pytest

from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import Deduction, TaxProfile, ValidationError
from hacienda_ai.rules import evaluate_deduction
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


def test_loads_seed_deductions_with_normalized_schema():
    deductions = load_deductions()
    assert {deduction.id for deduction in deductions} == {
        "es_cuotas_sindicales_2025_pendiente",
        "es_donativos_2025_pendiente",
    }
    assert all(deduction.sources for deduction in deductions)


def test_rejects_deduction_without_source():
    with pytest.raises(ValidationError, match="al menos una fuente"):
        validated_deduction(sources=[])


def test_rejects_unsupported_operator():
    with pytest.raises(ValidationError, match="Operador"):
        validated_deduction(requirements=[{"field": "x", "operator": "contains", "value": 1}])


def test_pending_source_deduction_is_not_recommended_directly():
    deduction = load_deductions()[0]
    result = evaluate_deduction(deduction, profile(expenses={"union_dues_amount": 50.0}))
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
    with pytest.raises(ValidationError, match="tax_profile.tax_year"):
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
