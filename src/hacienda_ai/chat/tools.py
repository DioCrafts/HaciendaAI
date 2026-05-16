"""Definiciones y dispatch de las herramientas expuestas al LLM.

Cada `Tool` tiene un `name`, una `description` para el LLM, un
`input_schema` JSON-Schema y un `handler` Python que ejecuta el trabajo
real. Los handlers son funciones puras sobre el corpus + registry +
escalas: no llaman al LLM ni al exterior, lo que hace todo el sistema
auditable y determinista.

El LLM nunca recibe el corpus completo en el prompt: lo consulta a través
de `get_deduction_catalog` y `search_norma`, que devuelven solo los
metadatos relevantes (id, name, boe_id, article). Esto mantiene el
contexto pequeño y obliga al modelo a citar exactamente.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..deductions import load_deductions
from ..irpf import compute_quota, load_tax_scales
from ..irpf.quota import quota_to_dict
from ..irpf.scales import TaxScale
from ..models import Deduction, NormaRegistry, TaxProfile, ValidationError
from ..normas import load_norma_registry
from ..rules import evaluate_deductions
from ..safety import verify_citations

ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler

    def to_spec(self) -> dict[str, Any]:
        """Formato exigido por la API de Anthropic en `tools=[...]`."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


@dataclass
class ToolRegistry:
    """Conjunto de tools con dispatch unificado.

    Devuelve siempre un `dict` para que el resultado sea JSON-serializable
    y, ante errores controlados, devuelve `{"error": "..."}` en lugar de
    propagar excepciones — el LLM ve el error y puede reformular sin
    romper el loop.
    """

    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        if tool.name in self.tools:
            raise ValueError(f"Tool ya registrada: {tool.name}")
        self.tools[tool.name] = tool

    @property
    def specs(self) -> list[dict[str, Any]]:
        return [t.to_spec() for t in self.tools.values()]

    def dispatch(self, name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None:
            return {"error": f"Tool desconocida: {name}"}
        try:
            return tool.handler(tool_input)
        except ValidationError as exc:
            return {"error": f"Datos inválidos: {exc}"}
        except Exception as exc:  # noqa: BLE001 — superficie hacia el LLM
            return {"error": f"Fallo interno ejecutando {name}: {exc}"}


# ---------- Handlers ----------


def _deduction_summary(d: Deduction) -> dict[str, Any]:
    return {
        "id": d.id,
        "name": d.name,
        "category": d.category.value,
        "scope": d.scope.value,
        "region": d.region,
        "tax_year": d.tax_year,
        "effective_from": d.effective_from.isoformat() if d.effective_from else None,
        "effective_to": d.effective_to.isoformat() if d.effective_to else None,
        "sources": [
            {
                "boe_id": s.boe_id,
                "article": s.article,
                "paragraph": s.paragraph,
                "title": s.title,
            }
            for s in d.sources
        ],
    }


def _make_get_deduction_catalog(deductions: list[Deduction]) -> ToolHandler:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        tax_year = args.get("tax_year")
        region = args.get("region")
        scope = args.get("scope")
        category = args.get("category")
        result = []
        for d in deductions:
            if tax_year is not None and d.tax_year != tax_year:
                continue
            if region and (d.region or "").lower() != region.lower() and d.scope.value != "estatal":
                continue
            if scope and d.scope.value != scope:
                continue
            if category and d.category.value != category:
                continue
            result.append(_deduction_summary(d))
        return {"count": len(result), "deductions": result}

    return handler


def _make_search_norma(deductions: list[Deduction], registry: NormaRegistry) -> ToolHandler:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        query = (args.get("query") or "").strip().lower()
        if not query:
            return {"error": "El parámetro 'query' es obligatorio"}
        matches: list[dict[str, Any]] = []
        for d in deductions:
            if (
                query in d.id.lower()
                or query in d.name.lower()
                or query in d.description.lower()
                or any(query in (s.title or "").lower() for s in d.sources)
            ):
                matches.append(_deduction_summary(d))
        normas: list[dict[str, Any]] = []
        for boe_id, norma in registry._normas.items():  # noqa: SLF001 — lectura controlada
            if query in boe_id.lower() or query in norma.title.lower():
                normas.append(
                    {
                        "boe_id": boe_id,
                        "title": norma.title,
                        "kind": norma.kind.value,
                        "enacted_at": norma.enacted_at.isoformat(),
                    }
                )
        return {
            "deduction_matches": matches[:25],
            "norma_matches": normas[:25],
            "truncated": len(matches) > 25 or len(normas) > 25,
        }

    return handler


def _make_evaluate_profile(
    deductions: list[Deduction], registry: NormaRegistry
) -> ToolHandler:
    ded_by_id = {d.id: d for d in deductions}

    def handler(args: dict[str, Any]) -> dict[str, Any]:
        raw_profile = args.get("profile")
        if not isinstance(raw_profile, dict):
            return {"error": "'profile' debe ser un objeto TaxProfile JSON"}
        try:
            profile = TaxProfile.from_dict(raw_profile)
        except ValidationError as exc:
            return {"error": f"Perfil inválido: {exc}"}
        evaluations = evaluate_deductions(deductions, profile, registry)
        items: list[dict[str, Any]] = []
        for ev in evaluations:
            d = ded_by_id.get(ev.deduction_id)
            items.append(
                {
                    "deduction_id": ev.deduction_id,
                    "deduction_name": d.name if d else ev.deduction_id,
                    "status": ev.status,
                    "estimated_amount": ev.estimated_amount,
                    "reason": ev.reason,
                    "category": d.category.value if d else None,
                    "sources": [
                        {"boe_id": s.boe_id, "article": s.article}
                        for s in ev.sources
                    ],
                }
            )
        return {
            "devengo_date": profile.effective_devengo_date().isoformat(),
            "tax_year": profile.tax_year,
            "region": profile.region,
            "evaluations": items,
        }

    return handler


def _make_compute_irpf_quota(
    deductions: list[Deduction],
    registry: NormaRegistry,
    scales: list[TaxScale],
) -> ToolHandler:
    ded_by_id = {d.id: d for d in deductions}

    def handler(args: dict[str, Any]) -> dict[str, Any]:
        raw_profile = args.get("profile")
        if not isinstance(raw_profile, dict):
            return {"error": "'profile' debe ser un objeto TaxProfile JSON"}
        try:
            profile = TaxProfile.from_dict(raw_profile)
        except ValidationError as exc:
            return {"error": f"Perfil inválido: {exc}"}
        evaluations = evaluate_deductions(deductions, profile, registry)
        quota = compute_quota(profile, evaluations, ded_by_id, scales)
        return quota_to_dict(quota)

    return handler


def _make_verify_citation(
    deductions: list[Deduction],
    registry: NormaRegistry,
    scales: list[TaxScale],
) -> ToolHandler:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        text = args.get("text")
        if not isinstance(text, str):
            return {"error": "'text' (string) es obligatorio"}
        devengo_raw = args.get("devengo_date")
        devengo: date | None = None
        if isinstance(devengo_raw, str) and devengo_raw:
            try:
                devengo = date.fromisoformat(devengo_raw)
            except ValueError:
                return {"error": "devengo_date debe ser ISO 8601 (YYYY-MM-DD)"}
        result = verify_citations(
            text,
            corpus=deductions,
            scales=scales,
            registry=registry,
            devengo=devengo,
        )
        return {
            "verdict": result.verdict,
            "blocking_issues": [
                {"code": i.code, "message": i.message, "citation_raw": i.citation.raw}
                for i in result.blocking_issues
            ],
            "warnings": [
                {"code": i.code, "message": i.message, "citation_raw": i.citation.raw}
                for i in result.warnings
            ],
        }

    return handler


# ---------- Builder ----------


_PROFILE_SCHEMA = {
    "type": "object",
    "description": (
        "Perfil fiscal del contribuyente. Campos clave: tax_year (int), "
        "region (str), filing_mode, personal{}, family{}, income{} con "
        "claves como work_gross/work_net/base_imponible_general, "
        "withholdings[] y documents[]."
    ),
    "additionalProperties": True,
}


def build_default_registry(
    *,
    deductions: list[Deduction] | None = None,
    registry: NormaRegistry | None = None,
    scales: list[TaxScale] | None = None,
) -> ToolRegistry:
    """Construye el set de tools por defecto, cargando del disco si no se inyectan."""
    deductions = deductions if deductions is not None else load_deductions()
    registry = registry if registry is not None else load_norma_registry()
    scales = scales if scales is not None else load_tax_scales()

    reg = ToolRegistry()
    reg.register(
        Tool(
            name="get_deduction_catalog",
            description=(
                "Devuelve un listado del corpus de deducciones del IRPF auditado, "
                "filtrable por año, región (CCAA), scope (estatal/autonomico) y "
                "categoría (deduccion, reduccion, minimo_personal_familiar, etc.). "
                "Cada entrada incluye sus citas BOE pinpoint. Úsalo para saber qué "
                "deducciones puedes mencionar al usuario."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "tax_year": {"type": "integer", "minimum": 2000},
                    "region": {"type": "string"},
                    "scope": {"type": "string", "enum": ["estatal", "autonomico", "foral", "local"]},
                    "category": {
                        "type": "string",
                        "enum": [
                            "deduccion",
                            "reduccion",
                            "exencion",
                            "gasto_deducible",
                            "minimo_personal_familiar",
                            "compensacion",
                            "ajuste",
                        ],
                    },
                },
            },
            handler=_make_get_deduction_catalog(deductions),
        )
    )
    reg.register(
        Tool(
            name="search_norma",
            description=(
                "Busca una norma o deducción por palabra clave en su id, nombre, "
                "descripción o título de la fuente. Devuelve hasta 25 coincidencias "
                "de cada tipo. Útil cuando el usuario describe el supuesto sin saber "
                "el id."
            ),
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            handler=_make_search_norma(deductions, registry),
        )
    )
    reg.register(
        Tool(
            name="evaluate_profile",
            description=(
                "Evalúa el perfil fiscal contra TODO el corpus de deducciones y "
                "devuelve, por cada regla, si aplica, su importe estimado y la "
                "cita BOE de la fuente. Es la única forma legítima de saber qué "
                "deducciones aplican: nunca lo afirmes sin haber llamado a esta tool."
            ),
            input_schema={
                "type": "object",
                "properties": {"profile": _PROFILE_SCHEMA},
                "required": ["profile"],
            },
            handler=_make_evaluate_profile(deductions, registry),
        )
    )
    reg.register(
        Tool(
            name="compute_irpf_quota",
            description=(
                "Calcula la cuota IRPF completa del perfil: bases imponibles, "
                "mínimo personal y familiar, cuota íntegra estatal y autonómica "
                "(si hay escala registrada), cuota líquida y diferencial. Es la "
                "ÚNICA forma legítima de obtener un importe a pagar/devolver. "
                "Devuelve `None` en los campos autonómicos cuando no hay escala "
                "registrada para la CCAA; en ese caso debes explicárselo al "
                "usuario, no inventarte el número."
            ),
            input_schema={
                "type": "object",
                "properties": {"profile": _PROFILE_SCHEMA},
                "required": ["profile"],
            },
            handler=_make_compute_irpf_quota(deductions, registry, scales),
        )
    )
    reg.register(
        Tool(
            name="verify_citation",
            description=(
                "Verifica que todas las citas legales contenidas en un texto "
                "existen y están vigentes en la fecha del devengo. Devuelve "
                "`verdict='safe' | 'warn' | 'block'`. Llámala ANTES de devolver "
                "tu respuesta final al usuario: si el veredicto es `block`, "
                "reformula la respuesta eliminando las citas problemáticas."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "devengo_date": {
                        "type": "string",
                        "description": "Fecha ISO 8601 (YYYY-MM-DD)",
                    },
                },
                "required": ["text"],
            },
            handler=_make_verify_citation(deductions, registry, scales),
        )
    )
    return reg


def serialize_tool_result(payload: dict[str, Any]) -> str:
    """Serialización canónica para devolver el resultado al LLM como tool_result."""
    return json.dumps(payload, ensure_ascii=False, default=str)
