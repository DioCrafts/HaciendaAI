"""Detector proactivo de oportunidades fiscales.

Lee las evaluaciones de las reglas y construye sugerencias para el usuario:
para cada regla en estado `missing_data`, propone qué campos del perfil
rellenar y estima el ahorro fiscal potencial si esos campos se llenaran
con valores típicos.

El propósito es invertir el flujo del motor: en lugar de evaluar lo que
hay, sugerir lo que falta. Es lo que un asesor humano hace al revisar un
perfil incompleto: "¿tienes hijos < 3 años?", "¿pagaste guardería?",
"¿alquilas vivienda habitual?".

Limitaciones:
- Sólo se sugiere para reglas en `validation_status: validada`. Las
  pendientes no entran en sugerencias para no recomendar reglas
  no auditadas.
- El "valor potencial" usa heurísticas conservadoras (deduction.limit o
  un importe sintético). El ahorro REAL puede variar según el resto
  del perfil — la sugerencia es de orden de magnitud.
- Sólo se modelan reglas estatales con `category` en {DEDUCCION,
  REDUCCION}. Bonificaciones y gastos deducibles quedan fuera de
  momento (las primeras requieren cuota atribuible; los segundos se
  asumen pre-descontados en la base).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .logging_setup import get_logger
from .models import Deduction, DeductionCategory, RuleEvaluation, TaxProfile, ValidationStatus
from .rules import evaluate_deductions
from .tax_calculation import compute_tax_summary

_logger = get_logger("opportunities")

# Importe sintético usado para estimar el ahorro potencial cuando la regla
# no tiene un `limit` ni un `fixed_amount` explícito.
DEFAULT_SYNTHETIC_AMOUNT = 1_000.0


@dataclass(frozen=True)
class Opportunity:
    """Una sugerencia de campo a rellenar para activar una regla validada."""

    deduction_id: str
    missing_fields: tuple[str, ...]
    potential_amount: float
    potential_savings_estimate: float
    category: str
    rationale: str


def detect_opportunities(
    profile: TaxProfile,
    deductions: list[Deduction],
    evaluations: list[RuleEvaluation],
) -> list[Opportunity]:
    """Para cada regla en `missing_data` con `validation_status: validada`,
    estima el ahorro fiscal real si los datos faltantes se rellenaran con
    valores plausibles. Devuelve las oportunidades ordenadas por ahorro
    descendente."""
    baseline = compute_tax_summary(profile, deductions, evaluations)
    rules_by_id = {deduction.id: deduction for deduction in deductions}

    opportunities: list[Opportunity] = []
    for evaluation in evaluations:
        if evaluation.status != "missing_data":
            continue
        deduction = rules_by_id.get(evaluation.deduction_id)
        if deduction is None or deduction.validation_status != ValidationStatus.VALIDADA:
            continue
        if deduction.category not in {DeductionCategory.DEDUCCION, DeductionCategory.REDUCCION}:
            continue

        synthetic_amount = _synthetic_amount_for(deduction)
        hypothetical_profile = _fill_profile_fields(profile, evaluation.missing_fields, synthetic_amount, deduction)
        if hypothetical_profile is None:
            continue
        hypothetical_evaluations = evaluate_deductions(deductions, hypothetical_profile)
        hypothetical_summary = compute_tax_summary(hypothetical_profile, deductions, hypothetical_evaluations)
        savings = baseline.cuota_diferencial - hypothetical_summary.cuota_diferencial
        if savings <= 0:
            continue
        opportunities.append(
            Opportunity(
                deduction_id=deduction.id,
                missing_fields=evaluation.missing_fields,
                potential_amount=synthetic_amount,
                potential_savings_estimate=round(savings, 2),
                category=deduction.category.value,
                rationale=_build_rationale(deduction, synthetic_amount, savings),
            )
        )
    opportunities.sort(key=lambda item: (-item.potential_savings_estimate, item.deduction_id))
    _logger.info(
        "opportunities_detected",
        extra={
            "tax_year": profile.tax_year,
            "missing_data_rules": sum(1 for e in evaluations if e.status == "missing_data"),
            "actionable_opportunities": len(opportunities),
        },
    )
    return opportunities


def _synthetic_amount_for(deduction: Deduction) -> float:
    """Devuelve un importe plausible para estimar el ahorro potencial."""
    calc = deduction.calculation
    if calc.type == "fixed_amount" and calc.fixed_amount is not None:
        return float(calc.fixed_amount)
    if deduction.limit is not None:
        return float(deduction.limit)
    if calc.type == "prorated_fixed_amount" and calc.monthly_amount is not None:
        months = calc.months_cap or 12.0
        return float(calc.monthly_amount) * float(months)
    return DEFAULT_SYNTHETIC_AMOUNT


def _fill_profile_fields(
    profile: TaxProfile,
    paths: tuple[str, ...],
    amount: float,
    deduction: Deduction,
) -> TaxProfile | None:
    """Devuelve un perfil con los `missing_fields` rellenados con valores
    plausibles y los `required_documents` de la regla añadidos a
    `profile.documents`. Esto permite que la regla pase directamente a
    `applies` y `compute_tax_summary` la incorpore al cálculo."""
    new_profile = replace(
        profile,
        personal=dict(profile.personal),
        family=dict(profile.family),
        income=dict(profile.income),
        expenses=dict(profile.expenses) if isinstance(profile.expenses, dict) else profile.expenses,
        taxable_base=dict(profile.taxable_base),
        cuota=dict(profile.cuota),
        documents=list(profile.documents),
    )
    for path in paths:
        if not _set_path(new_profile, path, _guess_value(path, amount)):
            return None
    for required_document in deduction.required_documents:
        if required_document not in new_profile.documents:
            new_profile.documents.append(required_document)
    return new_profile


def _guess_value(path: str, amount: float) -> Any:
    """Heurística por el sufijo del path: booleanos para flags conocidos,
    meses (12) para *_months, números (el importe sintético) para el resto.
    """
    last_segment = path.rsplit(".", 1)[-1]
    if last_segment.endswith(("_required", "_qualifying", "_qualifying_flag")):
        return True
    if last_segment in {"is_eligible_maternity_deduction"}:
        return True
    if last_segment in {"large_family_category"}:
        return "general"
    if last_segment.endswith("_months") or last_segment.endswith("child_months"):
        # 12 meses: año completo, valor razonable para un mes-mensaje.
        return 12
    return amount


def _set_path(profile: TaxProfile, path: str, value: Any) -> bool:
    """Establece `profile.<path>` = value, creando los dicts intermedios.
    Devuelve False si el camino no encaja en la estructura del perfil."""
    parts = path.split(".")
    if not parts:
        return False
    root_name, *rest = parts
    root: Any = getattr(profile, root_name, None)
    if not isinstance(root, dict):
        return False
    current = root
    for segment in rest[:-1]:
        nested = current.get(segment)
        if not isinstance(nested, dict):
            nested = {}
        current[segment] = nested
        current = nested
    current[rest[-1]] = value
    return True


def _build_rationale(deduction: Deduction, synthetic_amount: float, savings: float) -> str:
    return (
        f"Si rellenas los datos requeridos por '{deduction.name}' "
        f"(p.ej. importe de referencia {synthetic_amount:.2f} €), el motor "
        f"estima un ahorro fiscal real próximo a {savings:.2f} € en la cuota "
        f"diferencial. Importe orientativo — el ahorro definitivo depende del resto del perfil."
    )
