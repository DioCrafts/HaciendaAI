"""Motor determinista de reglas fiscales.

Este motor no interpreta texto libre: evalúa requisitos estructurados, calcula
solo fórmulas declaradas y devuelve estados auditables. Aplica un filtro
temporal por vigencia (de la deducción y, si se proporciona, de las normas
citadas vía `NormaRegistry`) ANTES de cualquier otra comprobación.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .models import (
    Deduction,
    NormaRegistry,
    NormaStatus,
    RiskLiteral,
    RuleEvaluation,
    RuleStatus,
    TaxProfile,
    ValidationStatus,
)

RISK_MAP: dict[str, RiskLiteral] = {"bajo": "low", "medio": "medium", "alto": "high"}


def evaluate_deduction(
    deduction: Deduction,
    profile: TaxProfile,
    registry: NormaRegistry | None = None,
) -> RuleEvaluation:
    """Evalúa una deducción contra un perfil fiscal estructurado.

    Si `registry` está presente, se consulta el estado y la vigencia de cada
    norma citada en la fecha del devengo. Una norma derogada o declarada
    inconstitucional bloquea la aplicación aunque los requisitos se cumplan;
    una suspendida degrada a `pending_validation`.
    """
    devengo = profile.effective_devengo_date()

    temporal = _check_deduction_vigencia(deduction, devengo)
    if temporal is not None:
        return temporal

    if registry is not None:
        blocking = _check_norma_status(deduction, registry, devengo)
        if blocking is not None:
            return blocking

    if deduction.tax_year != profile.tax_year:
        return _result(
            deduction,
            "does_not_apply",
            "La deducción pertenece a otro ejercicio fiscal.",
            confidence=0.9,
        )
    if deduction.region and deduction.region.lower() != profile.region.lower():
        return _result(
            deduction,
            "does_not_apply",
            "La deducción pertenece a otra comunidad autónoma.",
            confidence=0.9,
        )
    if deduction.validation_status != ValidationStatus.VALIDADA:
        return _result(
            deduction,
            "pending_validation",
            "La regla no está validada con fuente y tests suficientes; "
            "no debe recomendarse su aplicación directa.",
            confidence=0.2,
        )

    missing_fields: list[str] = []
    failed: list[str] = []
    facts = profile.to_dict()
    for requirement in deduction.requirements:
        found, value = get_path(facts, requirement.field)
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

    missing_documents = tuple(
        doc for doc in deduction.required_documents if doc not in profile.documents
    )
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


def evaluate_deductions(
    deductions: list[Deduction],
    profile: TaxProfile,
    registry: NormaRegistry | None = None,
) -> list[RuleEvaluation]:
    return [evaluate_deduction(deduction, profile, registry) for deduction in deductions]


def _check_deduction_vigencia(deduction: Deduction, devengo: date) -> RuleEvaluation | None:
    if deduction.effective_from is not None and devengo < deduction.effective_from:
        return _result(
            deduction,
            "does_not_apply",
            f"La deducción no estaba en vigor en el devengo ({devengo.isoformat()}); "
            f"entró en vigor el {deduction.effective_from.isoformat()}.",
            confidence=0.95,
        )
    if deduction.effective_to is not None and devengo > deduction.effective_to:
        return _result(
            deduction,
            "does_not_apply",
            f"La deducción dejó de estar en vigor el {deduction.effective_to.isoformat()}, "
            f"antes del devengo ({devengo.isoformat()}).",
            confidence=0.95,
        )
    return None


def _check_norma_status(
    deduction: Deduction,
    registry: NormaRegistry,
    devengo: date,
) -> RuleEvaluation | None:
    for source in deduction.sources:
        if source.boe_id is None or not registry.knows(source.boe_id):
            continue
        version = registry.version_at(source.boe_id, devengo)
        if version is None:
            return _result(
                deduction,
                "pending_validation",
                f"No consta versión registrada de {source.boe_id} en el devengo "
                f"({devengo.isoformat()}); requiere revisión humana.",
                confidence=0.2,
            )
        if version.status == NormaStatus.DEROGADA:
            return _result(
                deduction,
                "does_not_apply",
                f"La norma {source.boe_id} estaba derogada en el devengo "
                f"({devengo.isoformat()}).",
                confidence=0.95,
            )
        if version.status == NormaStatus.INCONSTITUCIONAL:
            return _result(
                deduction,
                "does_not_apply",
                f"La norma {source.boe_id} fue declarada inconstitucional con efectos "
                f"anteriores al devengo ({devengo.isoformat()}).",
                confidence=0.95,
            )
        if version.status == NormaStatus.SUSPENDIDA:
            return _result(
                deduction,
                "pending_validation",
                f"La norma {source.boe_id} estaba suspendida en el devengo "
                f"({devengo.isoformat()}); requiere revisión humana.",
                confidence=0.3,
            )
    return None


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
        return bool(value == expected)
    if operator == "!=":
        return bool(value != expected)
    if operator == "in":
        return bool(value in expected) if isinstance(expected, list | tuple | set) else False
    if operator in {">", ">=", "<", "<="}:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        if not isinstance(expected, (int, float)) or isinstance(expected, bool):
            return False
        comparisons = {
            ">": value > expected,
            ">=": value >= expected,
            "<": value < expected,
            "<=": value <= expected,
        }
        return comparisons[operator]
    return False


def _result(
    deduction: Deduction,
    status: RuleStatus,
    reason: str,
    estimated_amount: float = 0.0,
    missing_fields: tuple[str, ...] = (),
    missing_documents: tuple[str, ...] = (),
    confidence: float = 0.0,
) -> RuleEvaluation:
    return RuleEvaluation(
        deduction_id=deduction.id,
        status=status,
        estimated_amount=estimated_amount,
        reason=reason,
        missing_fields=missing_fields,
        missing_documents=missing_documents,
        sources=deduction.sources,
        risk_level=RISK_MAP[deduction.risk_level.value],
        confidence=confidence,
    )
