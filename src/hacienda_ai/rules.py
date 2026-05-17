"""Motor determinista de reglas fiscales.

Este motor no interpreta texto libre: evalúa requisitos estructurados, calcula
solo fórmulas declaradas y devuelve estados auditables. Aplica un filtro
temporal por vigencia (de la deducción y, si se proporciona, de las normas
citadas vía `NormaRegistry`) ANTES de cualquier otra comprobación.
"""

from __future__ import annotations

import logging
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

_LOG = logging.getLogger(__name__)

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
    if deduction.calculation.type == "manual_review":
        # Requisitos estructurados y justificantes correctos, pero la fórmula no
        # es lineal (escalado por base imponible, tipo de obra, fidelización
        # de donativos…). El motor no la modela todavía; devolverla como
        # `applies` con `estimated_amount=0` era engañoso: el asesor leía
        # "Aplica · 0,00 €" y descartaba la deducción. Surfacear como estado
        # propio mantiene visible que la regla aplica pero el importe lo
        # tiene que calcular un humano sobre la fuente citada.
        return _result(
            deduction,
            "requires_manual_calculation",
            "Se cumplen los requisitos estructurados y constan los "
            "justificantes, pero el motor no modela la fórmula no lineal de "
            "esta deducción. El importe debe calcularlo el asesor sobre la "
            "fuente citada.",
            estimated_amount=0.0,
            confidence=0.6,
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
    """Evalúa todas las deducciones contra el perfil y resuelve incompatibilidades.

    El bucle por deducción es aislado por diseño (cada regla independiente),
    pero algunas deducciones son mutuamente excluyentes por ley (p. ej.
    familia numerosa categoría general vs. especial; alquiler de vivienda
    habitual vs. inversión en vivienda habitual en el régimen transitorio).
    `resolve_incompatibilities` detecta esos conflictos POST-evaluación y
    degrada a `requires_user_choice` cuando dos o más deducciones aplicarían
    sobre el mismo supuesto.
    """
    evaluations = [evaluate_deduction(deduction, profile, registry) for deduction in deductions]
    return resolve_incompatibilities(evaluations, deductions)


def resolve_incompatibilities(
    evaluations: list[RuleEvaluation],
    deductions: list[Deduction],
) -> list[RuleEvaluation]:
    """Detecta conflictos de exclusividad entre deducciones `applies` y los
    degrada a `requires_user_choice` con la lista de alternativas y sus
    importes.

    Reglas:
      1. Solo se procesan las evaluaciones con `status == "applies"`. El
         resto se devuelve intacto (un `missing_data` o `pending_validation`
         no compite con nada).
      2. La relación de incompatibilidad se hace SIMÉTRICA aunque el JSON
         solo la declare en un sentido. Es la rama segura: si A dice ser
         incompatible con B, también B lo es con A para el contribuyente.
      3. Se computa el cierre transitivo: si A↔B y B↔C, las tres compiten.
      4. Si dentro de un componente conexo hay 2+ deducciones `applies`,
         TODAS pasan a `requires_user_choice`; el reason enumera las
         alternativas con sus importes estimados (sin decidir por el
         contribuyente — esa es competencia del asesor humano).
      5. Incompatibilidades que apuntan a IDs no presentes en el corpus
         evaluado se ignoran silenciosamente (caso típico: deducción
         autonómica que cita una estatal no incluida en el lote evaluado).

    El motor no decide cuál es la "mejor" deducción — informar al asesor
    de las opciones y sus importes ya elimina el error de coste real.
    """
    deduction_by_id = {d.id: d for d in deductions}
    applies_by_id: dict[str, RuleEvaluation] = {
        e.deduction_id: e for e in evaluations if e.status == "applies"
    }
    if len(applies_by_id) < 2:
        return list(evaluations)

    # Grafo simétrico de incompatibilidades restringido a las que aplican.
    graph: dict[str, set[str]] = {dx: set() for dx in applies_by_id}
    for dx_id in applies_by_id:
        deduction = deduction_by_id.get(dx_id)
        if deduction is None:
            continue
        for incompatible_id in deduction.incompatibilities:
            if incompatible_id in applies_by_id and incompatible_id != dx_id:
                graph[dx_id].add(incompatible_id)
                graph[incompatible_id].add(dx_id)

    # Componentes conexos (BFS). Cada componente con tamaño >=2 es un
    # conflicto que hay que resolver.
    conflicting: set[str] = set()
    components: list[set[str]] = []
    visited: set[str] = set()
    for start in graph:
        if start in visited or not graph[start]:
            continue
        component: set[str] = set()
        queue = [start]
        while queue:
            node = queue.pop()
            if node in visited:
                continue
            visited.add(node)
            component.add(node)
            queue.extend(graph[node] - visited)
        if len(component) >= 2:
            components.append(component)
            conflicting.update(component)

    if not conflicting:
        return list(evaluations)

    # Mapa de id → componente al que pertenece, para componer reason.
    component_of: dict[str, set[str]] = {}
    for comp in components:
        for member in comp:
            component_of[member] = comp

    resolved: list[RuleEvaluation] = []
    for evaluation in evaluations:
        if evaluation.deduction_id not in conflicting:
            resolved.append(evaluation)
            continue
        comp = component_of[evaluation.deduction_id]
        alternatives = sorted(
            (
                (
                    other_id,
                    deduction_by_id[other_id].name
                    if other_id in deduction_by_id
                    else other_id,
                    applies_by_id[other_id].estimated_amount,
                )
                for other_id in comp
                if other_id != evaluation.deduction_id
            ),
            key=lambda item: item[2],
            reverse=True,
        )
        alt_text = "; ".join(
            f"{name} ({alt_id}) → {amount:.2f} €" for alt_id, name, amount in alternatives
        )
        reason = (
            "Esta deducción es mutuamente excluyente con otra(s) que también "
            "aplicarían según el perfil. El contribuyente solo puede elegir "
            "una. Alternativas: "
            f"{alt_text}. Importe propio si se eligiera esta: "
            f"{evaluation.estimated_amount:.2f} €."
        )
        resolved.append(
            RuleEvaluation(
                deduction_id=evaluation.deduction_id,
                status="requires_user_choice",
                estimated_amount=0.0,
                reason=reason,
                missing_fields=evaluation.missing_fields,
                missing_documents=evaluation.missing_documents,
                sources=evaluation.sources,
                risk_level=evaluation.risk_level,
                confidence=evaluation.confidence,
            )
        )
    return resolved


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
        if source.boe_id is None:
            continue
        if not registry.knows(source.boe_id):
            # La norma citada no está en el registry: el filtro de vigencia no
            # se puede aplicar. Es un agujero de garantía, no un caso legítimo,
            # así que lo emitimos como WARN en lugar de seguir en silencio. La
            # evaluación continúa para no romper el flujo, pero el operador
            # debería ver esto en logs y completar el registry.
            _LOG.warning(
                "norma citada no registrada en NormaRegistry: "
                "deduction_id=%s boe_id=%s devengo=%s; "
                "filtro de vigencia por norma omitido para esta cita",
                deduction.id,
                source.boe_id,
                devengo.isoformat(),
            )
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
    if calculation.type == "tiered_progressive":
        return _calculate_tiered_progressive(deduction, facts)
    return 0.0


def _calculate_tiered_progressive(deduction: Deduction, facts: dict[str, Any]) -> float:
    """Aplica una escala progresiva por tramos sobre `base_field`.

    Cada tramo cubre la porción de la base entre el `up_to` anterior y el
    suyo; se le aplica `percentage` (o `alternate_percentage` si el campo
    declarado en `alternate_when_field` existe y es `True`). Tras sumar
    todos los tramos, se aplica:

    1. `cap` y `limit` declarados (techo absoluto), si existen.
    2. `cap_field` + `cap_percentage` (techo dinámico relativo a un campo
       del perfil, p. ej. 10 % de la base liquidable para donativos
       Ley 49/2002). Si `cap_field` está pero el valor no aparece en el
       perfil, la garantía `requirement: cap_field exists` declarada en
       el JSON debería haber atajado antes con `missing_data`; si por la
       razón que sea no está, devolvemos sin cap dinámico —es la rama
       conservadora (no infraestima, no sobreestima sin evidencia).
    """
    calculation = deduction.calculation
    if calculation.base_field is None or not calculation.tiers:
        return 0.0
    found, raw = get_path(facts, calculation.base_field)
    if not found or not isinstance(raw, (int, float)) or isinstance(raw, bool):
        return 0.0
    base = float(raw)
    if base <= 0:
        return 0.0

    amount = 0.0
    previous_threshold = 0.0
    for tier in calculation.tiers:
        if tier.up_to is None:
            tier_base = max(0.0, base - previous_threshold)
        else:
            tier_base = max(0.0, min(base, tier.up_to) - previous_threshold)
            previous_threshold = tier.up_to
        if tier_base <= 0:
            continue
        percentage = tier.percentage
        if tier.alternate_when_field is not None:
            alt_found, alt_value = get_path(facts, tier.alternate_when_field)
            if alt_found and alt_value is True and tier.alternate_percentage is not None:
                percentage = tier.alternate_percentage
        amount += tier_base * percentage

    fixed_caps = [c for c in [calculation.cap, deduction.limit] if c is not None]
    if fixed_caps:
        amount = min(amount, *fixed_caps)

    if calculation.cap_field is not None and calculation.cap_percentage is not None:
        cap_found, cap_value = get_path(facts, calculation.cap_field)
        if cap_found and isinstance(cap_value, (int, float)) and not isinstance(
            cap_value, bool
        ):
            amount = min(amount, float(cap_value) * calculation.cap_percentage)

    return amount


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
