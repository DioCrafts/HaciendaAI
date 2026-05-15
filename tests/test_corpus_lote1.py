"""Tests fiscales del lote 1 de deducciones estatales.

Las reglas se cargan desde el JSON con validation_status='pendiente_tests'.
Para verificar la lógica del motor sobre la regla real, hacemos un flip
local a 'validada' con dataclasses.replace — el JSON no se toca.

Cuando estas pruebas pasen y un asesor fiscal verifique importes/límites
contra el Manual práctico de Renta AEAT 2025, basta con cambiar
validation_status en el JSON a 'validada' para que el motor empiece a
recomendar la regla.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import Deduction, TaxProfile, ValidationStatus
from hacienda_ai.rules import evaluate_deduction


def _load_validated(deduction_id: str) -> Deduction:
    """Devuelve la deducción del corpus con validation_status forzado a VALIDADA."""
    deductions = {d.id: d for d in load_deductions()}
    return replace(deductions[deduction_id], validation_status=ValidationStatus.VALIDADA)


def _profile(**overrides: Any) -> TaxProfile:
    data: dict[str, Any] = {
        "tax_year": 2025,
        "region": "Madrid",
        "income": {"work_income": 30000.0},
        "expenses": {},
        # Por defecto alto, para que el cap del 30 % en planes de pensiones no
        # haga binding salvo en los tests que lo bajan a propósito.
        "taxable_base": {"net_work_and_economic_income": 30000.0},
        "documents": [],
    }
    data.update(overrides)
    return TaxProfile.from_dict(data)


# ---------- es_cuotas_sindicales_2025 ----------


def test_union_dues_applies_when_paid_and_evidenced() -> None:
    deduction = _load_validated("es_cuotas_sindicales_2025")
    profile = _profile(
        expenses={"union_dues_amount": 180.0},
        documents=["Justificante de pago de cuotas sindicales"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "applies"
    assert result.estimated_amount == 180.0


def test_union_dues_requires_work_income() -> None:
    deduction = _load_validated("es_cuotas_sindicales_2025")
    profile = _profile(income={}, expenses={"union_dues_amount": 180.0})
    result = evaluate_deduction(deduction, profile)
    assert result.status == "missing_data"
    assert "income.work_income" in result.missing_fields


def test_union_dues_missing_evidence_keeps_amount_but_blocks_recommendation() -> None:
    deduction = _load_validated("es_cuotas_sindicales_2025")
    profile = _profile(expenses={"union_dues_amount": 180.0}, documents=[])
    result = evaluate_deduction(deduction, profile)
    assert result.status == "missing_evidence"
    assert result.estimated_amount == 180.0
    assert result.missing_documents == ("Justificante de pago de cuotas sindicales",)


# ---------- es_cuotas_colegios_profesionales_2025 ----------


def test_professional_association_applies_when_required_and_capped_at_500() -> None:
    deduction = _load_validated("es_cuotas_colegios_profesionales_2025")
    profile = _profile(
        personal={"professional_association_required": True},
        expenses={"professional_association_fees_amount": 720.0},
        documents=["Justificante de cuotas colegiales"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "applies"
    assert result.estimated_amount == 500.0


def test_professional_association_does_not_apply_when_membership_is_not_required() -> None:
    deduction = _load_validated("es_cuotas_colegios_profesionales_2025")
    profile = _profile(
        personal={"professional_association_required": False},
        expenses={"professional_association_fees_amount": 200.0},
        documents=["Justificante de cuotas colegiales"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "does_not_apply"


# ---------- es_aportaciones_plan_pensiones_individual_2025 ----------


def test_pension_plan_individual_applies_below_cap() -> None:
    deduction = _load_validated("es_aportaciones_plan_pensiones_individual_2025")
    profile = _profile(
        expenses={"pension_plan_contribution_amount": 1200.0},
        documents=["Certificado de aportación al plan de pensiones"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "applies"
    assert result.estimated_amount == 1200.0


def test_pension_plan_individual_caps_amount_at_1500() -> None:
    deduction = _load_validated("es_aportaciones_plan_pensiones_individual_2025")
    profile = _profile(
        expenses={"pension_plan_contribution_amount": 4000.0},
        documents=["Certificado de aportación al plan de pensiones"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "applies"
    assert result.estimated_amount == 1500.0  # cap absoluto art. 52.1 LIRPF


def test_pension_plan_individual_caps_at_30_percent_of_net_work_income() -> None:
    """Aportación bajo el límite absoluto pero por encima del 30 % de
    rendimientos netos del trabajo y de actividades económicas: aplica
    sólo el 30 % (art. 52.1 LIRPF)."""
    deduction = _load_validated("es_aportaciones_plan_pensiones_individual_2025")
    profile = _profile(
        expenses={"pension_plan_contribution_amount": 1500.0},
        taxable_base={"net_work_and_economic_income": 4000.0},
        documents=["Certificado de aportación al plan de pensiones"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "applies"
    assert result.estimated_amount == 1200.0  # 4000 * 0.30


def test_pension_plan_individual_takes_lower_of_both_caps() -> None:
    """Si los dos límites son inferiores a la aportación, se aplica el menor."""
    deduction = _load_validated("es_aportaciones_plan_pensiones_individual_2025")
    profile = _profile(
        expenses={"pension_plan_contribution_amount": 5000.0},
        taxable_base={"net_work_and_economic_income": 2000.0},
        documents=["Certificado de aportación al plan de pensiones"],
    )
    result = evaluate_deduction(deduction, profile)
    # min(5000, 1500_absoluto, 600_relativo) = 600
    assert result.estimated_amount == 600.0


def test_pension_plan_individual_returns_missing_data_without_net_income() -> None:
    deduction = _load_validated("es_aportaciones_plan_pensiones_individual_2025")
    profile = _profile(
        expenses={"pension_plan_contribution_amount": 1200.0},
        taxable_base={},
        documents=["Certificado de aportación al plan de pensiones"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "missing_data"
    assert "taxable_base.net_work_and_economic_income" in result.missing_fields


def test_pension_plan_individual_is_validada_in_corpus() -> None:
    """La regla ya no es pendiente_tests: contrastada con LIRPF art. 52
    y promovida tras revisión humana."""
    deductions = {d.id: d for d in load_deductions()}
    deduction = deductions["es_aportaciones_plan_pensiones_individual_2025"]
    assert deduction.validation_status == ValidationStatus.VALIDADA
    assert deduction.last_reviewed_at is not None
    assert all(source.checked_at is not None for source in deduction.sources)


# ---------- es_aportaciones_plan_pensiones_conyuge_2025 ----------


def test_pension_plan_spouse_applies_when_spouse_net_income_below_8000() -> None:
    deduction = _load_validated("es_aportaciones_plan_pensiones_conyuge_2025")
    profile = _profile(
        family={"spouse": {"net_work_and_economic_income": 5000.0}},
        expenses={"spouse_pension_plan_contribution_amount": 800.0},
        documents=["Certificado de aportación al plan de pensiones del cónyuge"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "applies"
    assert result.estimated_amount == 800.0


def test_pension_plan_spouse_does_not_apply_when_spouse_net_income_above_threshold() -> None:
    deduction = _load_validated("es_aportaciones_plan_pensiones_conyuge_2025")
    profile = _profile(
        family={"spouse": {"net_work_and_economic_income": 12000.0}},
        expenses={"spouse_pension_plan_contribution_amount": 800.0},
        documents=["Certificado de aportación al plan de pensiones del cónyuge"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "does_not_apply"


def test_pension_plan_spouse_caps_amount_at_1000() -> None:
    deduction = _load_validated("es_aportaciones_plan_pensiones_conyuge_2025")
    profile = _profile(
        family={"spouse": {"net_work_and_economic_income": 4000.0}},
        expenses={"spouse_pension_plan_contribution_amount": 2500.0},
        documents=["Certificado de aportación al plan de pensiones del cónyuge"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "applies"
    assert result.estimated_amount == 1000.0


def test_pension_plan_spouse_returns_missing_data_without_spouse_net_income() -> None:
    deduction = _load_validated("es_aportaciones_plan_pensiones_conyuge_2025")
    profile = _profile(
        family={},
        expenses={"spouse_pension_plan_contribution_amount": 500.0},
        documents=["Certificado de aportación al plan de pensiones del cónyuge"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "missing_data"
    assert "family.spouse.net_work_and_economic_income" in result.missing_fields


# ---------- propiedad transversal ----------


def test_all_lote1_rules_have_proper_sources_and_effective_range() -> None:
    deductions = {d.id: d for d in load_deductions()}
    lote1_ids = {
        "es_cuotas_sindicales_2025",
        "es_cuotas_colegios_profesionales_2025",
        "es_aportaciones_plan_pensiones_individual_2025",
        "es_aportaciones_plan_pensiones_conyuge_2025",
    }
    for deduction_id in lote1_ids:
        deduction = deductions[deduction_id]
        assert deduction.sources, f"{deduction_id} debe tener al menos una fuente"
        assert all(source.type == "ley" for source in deduction.sources), (
            f"{deduction_id} debe referenciar la ley aplicable"
        )
        assert deduction.effective_from == "2025-01-01"
        assert deduction.effective_to == "2025-12-31"


def test_all_lote1_rules_are_validada_in_corpus() -> None:
    """Las cuatro reglas del lote 1 están todas en validada tras la sesión
    de promoción de mayo de 2026. Cada una con last_reviewed_at y todos los
    sources con checked_at no nulo. Si alguna pasa a obsoleta o necesita
    re-revisión, este test debe relajarse explícitamente."""
    deductions = {d.id: d for d in load_deductions()}
    lote1_ids = (
        "es_cuotas_sindicales_2025",
        "es_cuotas_colegios_profesionales_2025",
        "es_aportaciones_plan_pensiones_individual_2025",
        "es_aportaciones_plan_pensiones_conyuge_2025",
    )
    for deduction_id in lote1_ids:
        deduction = deductions[deduction_id]
        assert deduction.validation_status == ValidationStatus.VALIDADA, (
            f"{deduction_id}: esperado validada, encontrado {deduction.validation_status.value}"
        )
        assert deduction.last_reviewed_at is not None, f"{deduction_id} debe tener last_reviewed_at"
        assert all(source.checked_at is not None for source in deduction.sources), (
            f"{deduction_id}: todas las fuentes deben tener checked_at"
        )
