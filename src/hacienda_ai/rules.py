"""Motor determinista de reglas fiscales.

Este motor no interpreta texto libre: evalúa requisitos estructurados, calcula
solo fórmulas declaradas y devuelve estados auditables.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

from .models import Deduction, RuleEvaluation, TaxProfile, Tier, ValidationStatus

RISK_MAP = {"bajo": "low", "medio": "medium", "alto": "high"}

TAXABLE_BASE_LIMIT_FIELDS: dict[str, str] = {
    "max_percentage_of_base_liquidable": "taxable_base.liquidable",
    "max_percentage_of_base_general": "taxable_base.general",
    "max_percentage_of_base_savings": "taxable_base.savings",
    "max_percentage_of_net_work_and_economic_income": "taxable_base.net_work_and_economic_income",
}


def evaluate_deduction(deduction: Deduction, profile: TaxProfile) -> RuleEvaluation:
    """Evalúa una deducción contra un perfil fiscal estructurado."""
    if deduction.tax_year != profile.tax_year:
        return _result(deduction, "does_not_apply", "La deducción pertenece a otro ejercicio fiscal.", confidence=0.9)
    if deduction.region and deduction.region.lower() != profile.region.lower():
        return _result(deduction, "does_not_apply", "La deducción pertenece a otra comunidad autónoma.", confidence=0.9)
    if not _within_effective_range(deduction, profile.tax_year):
        return _result(
            deduction, "does_not_apply", "La deducción no estaba en vigor durante el ejercicio fiscal.", confidence=0.9
        )
    if deduction.validation_status != ValidationStatus.VALIDADA:
        return _result(
            deduction,
            "pending_validation",
            "La regla no está validada con fuente y tests suficientes; no debe recomendarse su aplicación directa.",
            confidence=0.2,
        )

    missing_fields: list[str] = []
    failed: list[str] = []
    facts = profile.to_dict()
    for requirement in deduction.requirements:
        found, value = get_path(facts, requirement.field)
        if requirement.operator in {"exists", "not_exists"}:
            if not compare(value if found else None, requirement.operator, requirement.value):
                failed.append(requirement.field)
            continue
        if not found:
            missing_fields.append(requirement.field)
            continue
        if not compare(value, requirement.operator, requirement.value):
            failed.append(requirement.field)

    if missing_fields:
        return _result(
            deduction,
            "missing_data",
            "Faltan datos necesarios para evaluar la regla.",
            missing_fields=tuple(missing_fields),
            confidence=0.5,
        )
    if failed:
        return _result(
            deduction,
            "does_not_apply",
            "No se cumplen todos los requisitos estructurados.",
            confidence=0.8,
        )

    missing_documents = tuple(doc for doc in deduction.required_documents if doc not in profile.documents)
    amount = calculate_amount(deduction, facts)
    amount, missing_bases = _apply_taxable_base_limits(deduction, amount, facts)
    if missing_bases:
        return _result(
            deduction,
            "missing_data",
            "Faltan datos de la base imponible necesarios para aplicar el límite legal de la deducción.",
            missing_fields=tuple(missing_bases),
            confidence=0.5,
        )
    if missing_documents:
        return _result(
            deduction,
            "missing_evidence",
            "La regla parece aplicable, pero faltan justificantes documentales.",
            estimated_amount=amount,
            missing_documents=missing_documents,
            confidence=0.7,
        )
    return _result(
        deduction,
        "applies",
        "Se cumplen los requisitos estructurados y constan los documentos requeridos.",
        estimated_amount=amount,
        confidence=0.85,
    )


def evaluate_deductions(deductions: list[Deduction], profile: TaxProfile) -> list[RuleEvaluation]:
    evaluations = [evaluate_deduction(deduction, profile) for deduction in deductions]
    return _resolve_incompatibilities(deductions, evaluations)


def _within_effective_range(deduction: Deduction, tax_year: int) -> bool:
    year_start = date(tax_year, 1, 1)
    year_end = date(tax_year, 12, 31)
    if deduction.effective_from and date.fromisoformat(deduction.effective_from) > year_end:
        return False
    return not (deduction.effective_to and date.fromisoformat(deduction.effective_to) < year_start)


def _resolve_incompatibilities(
    deductions: list[Deduction],
    evaluations: list[RuleEvaluation],
) -> list[RuleEvaluation]:
    conflicts: dict[str, set[str]] = {}
    for deduction in deductions:
        conflicts.setdefault(deduction.id, set()).update(deduction.incompatibilities)
    for deduction_id, related in list(conflicts.items()):
        for other_id in related:
            conflicts.setdefault(other_id, set()).add(deduction_id)

    applying = [
        (deduction, evaluation)
        for deduction, evaluation in zip(deductions, evaluations, strict=False)
        if evaluation.status == "applies"
    ]
    applying.sort(key=lambda pair: (-pair[1].estimated_amount, pair[0].id))

    accepted: set[str] = set()
    losers: dict[str, str] = {}
    for deduction, _ in applying:
        clash = conflicts.get(deduction.id, set()) & accepted
        if clash:
            losers[deduction.id] = sorted(clash)[0]
        else:
            accepted.add(deduction.id)

    if not losers:
        return evaluations
    return [
        replace(
            evaluation,
            status="does_not_apply",
            estimated_amount=0.0,
            reason=f"Incompatible con la deducción {losers[deduction.id]}, que se prioriza por importe estimado más alto.",
        )
        if deduction.id in losers
        else evaluation
        for deduction, evaluation in zip(deductions, evaluations, strict=False)
    ]


def calculate_amount(deduction: Deduction, facts: dict[str, Any]) -> float:
    calculation = deduction.calculation
    if calculation.type == "manual_review":
        return 0.0
    if calculation.type == "fixed_amount":
        return float(calculation.fixed_amount or 0.0)
    if calculation.type == "amount_field":
        if not calculation.base_field:
            return 0.0
        found, value = get_path(facts, calculation.base_field)
        amount = float(value) if found and isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
        return min(amount, deduction.limit) if deduction.limit is not None else amount
    if calculation.type == "percentage_with_cap":
        if not calculation.base_field or calculation.percentage is None:
            return 0.0
        found, value = get_path(facts, calculation.base_field)
        base = float(value) if found and isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
        amount = base * calculation.percentage
        caps = [cap for cap in [calculation.cap, deduction.limit] if cap is not None]
        return min([amount, *caps]) if caps else amount
    if calculation.type == "tiered_percentage":
        if not calculation.base_field or not calculation.tiers:
            return 0.0
        found, value = get_path(facts, calculation.base_field)
        base = float(value) if found and isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
        amount = _apply_tiers(base, calculation.tiers)
        return min(amount, deduction.limit) if deduction.limit is not None else amount
    if calculation.type == "prorated_fixed_amount":
        if not calculation.months_field or calculation.monthly_amount is None:
            return 0.0
        found, value = get_path(facts, calculation.months_field)
        months = float(value) if found and isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
        months = max(months, 0.0)
        if calculation.months_cap is not None:
            months = min(months, calculation.months_cap)
        amount = months * calculation.monthly_amount
        return min(amount, deduction.limit) if deduction.limit is not None else amount
    return 0.0


def _apply_taxable_base_limits(deduction: Deduction, amount: float, facts: dict[str, Any]) -> tuple[float, list[str]]:
    if not deduction.taxable_base_limits:
        return amount, []
    caps: list[float] = []
    missing: list[str] = []
    for limit_key, percentage in deduction.taxable_base_limits.items():
        base_path = TAXABLE_BASE_LIMIT_FIELDS.get(limit_key)
        if base_path is None:
            continue
        found, value = get_path(facts, base_path)
        if not found or not isinstance(value, (int, float)) or isinstance(value, bool):
            missing.append(base_path)
            continue
        caps.append(float(value) * percentage)
    if missing:
        return 0.0, missing
    if not caps:
        return amount, []
    return min(amount, *caps), []


def _apply_tiers(base: float, tiers: tuple[Tier, ...]) -> float:
    total = 0.0
    remaining = base
    floor = 0.0
    for tier in tiers:
        if remaining <= 0:
            break
        if tier.up_to is None:
            chunk = remaining
        else:
            capacity = max(0.0, tier.up_to - floor)
            chunk = min(remaining, capacity)
            floor = tier.up_to
        total += chunk * tier.percentage
        remaining -= chunk
    return total


def get_path(data: dict[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = data
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def compare(value: Any, operator: str, expected: Any) -> bool:
    if operator == "exists":
        return value is not None
    if operator == "not_exists":
        return value is None
    if operator == "==":
        return bool(value == expected)
    if operator == "!=":
        return bool(value != expected)
    if operator == "in":
        return value in expected if isinstance(expected, list | tuple | set) else False
    if operator in {">", ">=", "<", "<="}:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        if not isinstance(expected, (int, float)) or isinstance(expected, bool):
            return False
        return {">": value > expected, ">=": value >= expected, "<": value < expected, "<=": value <= expected}[
            operator
        ]
    return False


def _result(
    deduction: Deduction,
    status: str,
    reason: str,
    estimated_amount: float = 0.0,
    missing_fields: tuple[str, ...] = (),
    missing_documents: tuple[str, ...] = (),
    confidence: float = 0.0,
) -> RuleEvaluation:
    return RuleEvaluation(
        deduction_id=deduction.id,
        status=status,  # type: ignore[arg-type]
        estimated_amount=estimated_amount,
        reason=reason,
        missing_fields=missing_fields,
        missing_documents=missing_documents,
        sources=deduction.sources,
        risk_level=RISK_MAP[deduction.risk_level.value],  # type: ignore[arg-type]
        confidence=confidence,
    )
