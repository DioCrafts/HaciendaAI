from __future__ import annotations

from typing import Any

from hacienda_ai.models import Deduction, TaxProfile
from hacienda_ai.simulator import simulate


def _deduction(**overrides: Any) -> Deduction:
    data: dict[str, Any] = {
        "id": "test_validada",
        "name": "Deducción validada de prueba",
        "description": "Regla sintética usada solo para probar el simulador.",
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
        "sources": [{"type": "test", "title": "Fuente sintética", "checked_at": "2026-05-11"}],
        "effective_from": "2025-01-01",
        "effective_to": "2025-12-31",
        "last_reviewed_at": "2026-05-11",
        "risk_level": "bajo",
        "validation_status": "validada",
    }
    data.update(overrides)
    return Deduction.from_dict(data)


def _profile(**overrides: Any) -> TaxProfile:
    data: dict[str, Any] = {
        "tax_year": 2025,
        "region": "Madrid",
        "expenses": {"test_amount": 120.0},
        "documents": ["Justificante de prueba"],
    }
    data.update(overrides)
    return TaxProfile.from_dict(data)


def test_simulate_returns_three_scenarios_per_filing_mode() -> None:
    report = simulate([_deduction()], _profile())
    assert {s.name for s in report.individual.scenarios} == {"conservador", "esperado", "optimizado"}
    assert {s.name for s in report.conjunta.scenarios} == {"conservador", "esperado", "optimizado"}
    assert all(s.filing_mode == "individual" for s in report.individual.scenarios)
    assert all(s.filing_mode == "conjunta" for s in report.conjunta.scenarios)


def test_conservative_scenario_includes_only_applies() -> None:
    applies_full = _deduction()
    missing_evidence = _deduction(id="missing_doc", required_documents=["Doc inexistente"])
    missing_data_only = _deduction(
        id="missing_field",
        requirements=[{"field": "expenses.nonexistent", "operator": ">", "value": 0}],
    )
    report = simulate([applies_full, missing_evidence, missing_data_only], _profile())
    conservative = next(s for s in report.individual.scenarios if s.name == "conservador")
    assert conservative.included_deduction_ids == ("test_validada",)
    assert conservative.total_estimated_amount == 100.0


def test_expected_scenario_includes_missing_evidence() -> None:
    applies_full = _deduction()
    missing_evidence = _deduction(
        id="missing_doc",
        required_documents=["Doc inexistente"],
        calculation={"type": "fixed_amount", "fixed_amount": 30.0},
    )
    report = simulate([applies_full, missing_evidence], _profile())
    expected = next(s for s in report.individual.scenarios if s.name == "esperado")
    assert set(expected.included_deduction_ids) == {"test_validada", "missing_doc"}
    assert expected.total_estimated_amount == 130.0


def test_optimistic_scenario_includes_missing_data() -> None:
    applies_full = _deduction()
    missing_data_only = _deduction(
        id="missing_field",
        requirements=[{"field": "expenses.nonexistent", "operator": ">", "value": 0}],
    )
    report = simulate([applies_full, missing_data_only], _profile())
    optimized = next(s for s in report.individual.scenarios if s.name == "optimizado")
    assert set(optimized.included_deduction_ids) == {"test_validada", "missing_field"}


def test_pending_validation_never_enters_any_scenario() -> None:
    pending = _deduction(id="pending_seed", validation_status="pendiente_fuente")
    report = simulate([pending], _profile())
    for scenario in (*report.individual.scenarios, *report.conjunta.scenarios):
        assert pending.id not in scenario.included_deduction_ids
        assert scenario.total_estimated_amount == 0.0


def test_recommends_filing_mode_with_higher_expected_amount() -> None:
    joint_only = _deduction(
        id="solo_conjunta",
        requirements=[
            {"field": "expenses.test_amount", "operator": ">", "value": 0},
            {"field": "filing_mode", "operator": "==", "value": "conjunta"},
        ],
        calculation={"type": "fixed_amount", "fixed_amount": 500.0},
        required_documents=[],
    )
    report = simulate([joint_only], _profile(filing_mode="individual"))
    assert report.recommended_filing_mode == "conjunta"

    individual_expected = next(s for s in report.individual.scenarios if s.name == "esperado")
    conjunta_expected = next(s for s in report.conjunta.scenarios if s.name == "esperado")
    assert individual_expected.total_estimated_amount == 0.0
    assert conjunta_expected.total_estimated_amount == 500.0


def test_filing_mode_does_not_mutate_input_profile() -> None:
    profile = _profile(filing_mode="individual")
    simulate([_deduction()], profile)
    assert profile.filing_mode == "individual"
