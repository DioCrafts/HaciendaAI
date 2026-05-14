"""Motor determinista de reglas fiscales.

Este motor no interpreta texto libre: evalúa requisitos estructurados, calcula
solo fórmulas declaradas y devuelve estados auditables.
"""

from __future__ import annotations

from typing import Any

from .models import Deduction, RuleEvaluation, TaxProfile, ValidationStatus

RISK_MAP = {"bajo": "low", "medio": "medium", "alto": "high"}


def evaluate_deduction(deduction: Deduction, profile: TaxProfile) -> RuleEvaluation:
    """Evalúa una deducción contra un perfil fiscal estructurado."""
    if deduction.tax_year != profile.tax_year:
        return _result(deduction, "does_not_apply", "La deducción pertenece a otro ejercicio fiscal.", confidence=0.9)
    if deduction.region and deduction.region.lower() != profile.region.lower():
        return _result(deduction, "does_not_apply", "La deducción pertenece a otra comunidad autónoma.", confidence=0.9)
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
    return [evaluate_deduction(deduction, profile) for deduction in deductions]


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
    return 0.0


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
        return value == expected
    if operator == "!=":
        return value != expected
    if operator == "in":
        return value in expected if isinstance(expected, list | tuple | set) else False
    if operator in {">", ">=", "<", "<="}:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        if not isinstance(expected, (int, float)) or isinstance(expected, bool):
            return False
        return {">": value > expected, ">=": value >= expected, "<": value < expected, "<=": value <= expected}[operator]
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
