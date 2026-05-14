"""Tests del corpus autonómico base.

Cubre las 15 CCAA de régimen común. País Vasco y Navarra quedan fuera
por tener régimen foral propio. Ceuta y Melilla quedan fuera porque no
tienen deducciones autonómicas como tales (aplican una bonificación
general del 60 % sobre la cuota).

Las reglas están como 'pendiente_fuente': estructura completa pero
sin importes verificados contra la normativa autonómica vigente. El
motor las carga, las filtra por región y devuelve pending_validation.
Cuando un asesor contraste los porcentajes/topes/edades/umbrales de
renta de una CCAA concreta, basta cambiar validation_status en el
JSON a 'validada'.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import Deduction, Scope, TaxProfile, ValidationStatus
from hacienda_ai.rules import evaluate_deduction

EXPECTED_REGIONS: frozenset[str] = frozenset(
    {
        "Andalucía",
        "Aragón",
        "Asturias",
        "Illes Balears",
        "Canarias",
        "Cantabria",
        "Castilla-La Mancha",
        "Castilla y León",
        "Cataluña",
        "Madrid",
        "Comunitat Valenciana",
        "Extremadura",
        "Galicia",
        "La Rioja",
        "Murcia",
    }
)


def _autonomic_deductions() -> list[Deduction]:
    return [d for d in load_deductions() if d.scope == Scope.AUTONOMICO]


def _profile(region: str, **overrides: Any) -> TaxProfile:
    data: dict[str, Any] = {
        "tax_year": 2025,
        "region": region,
        "personal": {"age": 28},
        "income": {"work_income": 22000.0},
        "expenses": {"rent_amount": 8000.0},
        "documents": ["Contrato de arrendamiento y justificantes de pago"],
    }
    data.update(overrides)
    return TaxProfile.from_dict(data)


def test_autonomic_corpus_covers_15_regimen_comun_ccaa() -> None:
    autonomic = _autonomic_deductions()
    regions = {d.region for d in autonomic}
    assert regions == EXPECTED_REGIONS
    assert len(autonomic) == 15


def test_all_autonomic_rules_have_region_and_2025_effective_range() -> None:
    for deduction in _autonomic_deductions():
        assert deduction.scope == Scope.AUTONOMICO
        assert deduction.region is not None and deduction.region != ""
        assert deduction.tax_year == 2025
        assert deduction.effective_from == "2025-01-01"
        assert deduction.effective_to == "2025-12-31"


def test_all_autonomic_rules_are_pendiente_fuente() -> None:
    """Las reglas son placeholders hasta que un asesor verifique los
    porcentajes/topes/edades/umbrales contra la normativa de cada CCAA."""
    for deduction in _autonomic_deductions():
        assert deduction.validation_status == ValidationStatus.PENDIENTE_FUENTE


def test_pendiente_fuente_returns_pending_validation_regardless_of_data() -> None:
    madrid_rule = next(d for d in _autonomic_deductions() if d.region == "Madrid")
    result = evaluate_deduction(madrid_rule, _profile("Madrid"))
    assert result.status == "pending_validation"
    assert result.estimated_amount == 0.0


def test_region_filter_excludes_other_ccaa() -> None:
    """La regla autonómica de Madrid no debe aplicar a un perfil de Cataluña
    aunque cumpla todos los demás requisitos."""
    madrid_rule = next(d for d in _autonomic_deductions() if d.region == "Madrid")
    # Flip a VALIDADA en local para que el filtro no quede oculto por el
    # short-circuit de pending_validation.
    validated_rule = replace(madrid_rule, validation_status=ValidationStatus.VALIDADA)
    result = evaluate_deduction(validated_rule, _profile("Cataluña"))
    assert result.status == "does_not_apply"
    assert "comunidad autónoma" in result.reason


def test_region_filter_matches_case_insensitively() -> None:
    """profile.region en minúscula debe casar con deduction.region."""
    madrid_rule = next(d for d in _autonomic_deductions() if d.region == "Madrid")
    validated_rule = replace(madrid_rule, validation_status=ValidationStatus.VALIDADA)
    result = evaluate_deduction(validated_rule, _profile("madrid"))
    # Sin la coincidencia de región el resultado sería does_not_apply.
    # Como es validada y los datos del perfil cumplen requisitos, el
    # resultado debe ser applies con el importe calculado.
    assert result.status == "applies"


def test_each_ccaa_rule_validated_locally_applies_to_its_own_region() -> None:
    """Smoke test: para cada CCAA, flipear su regla a VALIDADA en local
    y comprobar que aplica al perfil correspondiente."""
    for deduction in _autonomic_deductions():
        assert deduction.region is not None
        validated_rule = replace(deduction, validation_status=ValidationStatus.VALIDADA)
        result = evaluate_deduction(validated_rule, _profile(deduction.region))
        assert result.status == "applies", (
            f"La regla {deduction.id} no aplica para su propio perfil ({deduction.region}): {result.reason}"
        )
        assert result.estimated_amount > 0


def test_pais_vasco_and_navarra_are_not_covered() -> None:
    """Estas CCAA tienen régimen foral propio; no deben aparecer en el corpus."""
    regions = {d.region for d in _autonomic_deductions()}
    assert "País Vasco" not in regions
    assert "Navarra" not in regions
    assert "Ceuta" not in regions
    assert "Melilla" not in regions
