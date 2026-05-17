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
from ..fiscal_calendar import (
    TaxpayerSegment,
    get_upcoming_events,
    resolve_current_fiscal_year,
)
from ..irpf import compute_quota, load_tax_scales
from ..irpf.quota import quota_to_dict
from ..irpf.scales import TaxScale
from ..models import Deduction, NormaRegistry, TaxProfile, ValidationError
from ..normas import load_norma_registry
from ..rag.grounding import build_llm_context
from ..rag.vector import SourceType, VectorMatch, VectorQuery
from ..rules import evaluate_deductions
from ..safety import verify_citations
from .retriever import LegalContextRetriever

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


def _citation_hint(match: VectorMatch) -> str:
    """Genera la cita pinpoint sugerida para que el LLM la copie verbatim.

    Cada familia de fuente tiene su formato canónico distinto:

    - Norma: `art. 19.2.e) (BOE-A-2006-20764)`
    - Sentencia: `STS ECLI:ES:TS:2024:1234`
    - Consulta DGT: `Consulta DGT V0123-24 (30/01/2024)`
    - Resolución TEAC: `TEAC 00/12345/2023`
    - Manual AEAT: `Manual AEAT manual_irpf 2024`

    Vivir aquí —y no en `context_builder.py`— mantiene la responsabilidad
    en la capa que lo expone al LLM como tool result; el grounding sigue
    siendo agnóstico del formato de cita textual.
    """
    meta = match.chunk.metadata
    st = match.chunk.source_type
    if st == SourceType.NORMA:
        boe_id = meta.get("boe_id")
        articulo = meta.get("articulo")
        apartado = meta.get("apartado")
        pin = ""
        if articulo:
            pin = str(articulo)
            if apartado:
                pin = f"{pin}.{apartado}" if pin and not pin.endswith(".") else f"{pin}{apartado}"
        if boe_id and pin:
            return f"{pin} ({boe_id})"
        if boe_id:
            return str(boe_id)
        return pin
    if st == SourceType.SENTENCIA:
        ecli = meta.get("ecli")
        tribunal = meta.get("tribunal_codigo")
        if ecli and tribunal:
            return f"{tribunal} {ecli}"
        return str(ecli or tribunal or "")
    if st == SourceType.CONSULTA_DGT:
        numero = meta.get("numero")
        fecha = meta.get("fecha")
        if numero and fecha:
            return f"Consulta DGT {numero} ({fecha})"
        if numero:
            return f"Consulta DGT {numero}"
        return "Consulta DGT"
    if st == SourceType.RESOLUCION_TEAC:
        numero = meta.get("numero")
        organo = meta.get("organo")
        if numero and organo:
            return f"{str(organo).upper()} {numero}"
        return str(numero or organo or "")
    if st == SourceType.MANUAL:
        fuente = meta.get("fuente")
        ejercicio = meta.get("ejercicio")
        if fuente and ejercicio:
            return f"Manual AEAT {fuente} {ejercicio}"
        if fuente:
            return f"Manual AEAT {fuente}"
        return "Manual AEAT"
    return ""


_RETRIEVE_MIN_TOP_K = 1
_RETRIEVE_MAX_TOP_K = 25
_RETRIEVE_DEFAULT_TOP_K = 6
_SOURCE_TYPE_VALUES = tuple(st.value for st in SourceType)


def _make_retrieve_legal_context(retriever: LegalContextRetriever) -> ToolHandler:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        query_text = args.get("query")
        if not isinstance(query_text, str) or not query_text.strip():
            return {"error": "'query' (string no vacía) es obligatorio"}

        impuesto = args.get("impuesto")
        if impuesto is not None and (
            not isinstance(impuesto, str) or not impuesto.strip()
        ):
            return {"error": "'impuesto' debe ser string no vacío o estar ausente"}
        impuesto_eff: str | None = impuesto.strip().lower() if impuesto else None

        devengo_raw = args.get("devengo_date")
        devengo: date | None = None
        if devengo_raw is not None:
            if not isinstance(devengo_raw, str) or not devengo_raw:
                return {
                    "error": "'devengo_date' debe ser string ISO 8601 (YYYY-MM-DD)"
                }
            try:
                devengo = date.fromisoformat(devengo_raw)
            except ValueError:
                return {
                    "error": "'devengo_date' inválida; usa formato ISO 8601 (YYYY-MM-DD)"
                }

        source_types_raw = args.get("source_types")
        source_types: tuple[SourceType, ...] | None = None
        if source_types_raw is not None:
            if not isinstance(source_types_raw, list) or not source_types_raw:
                return {
                    "error": (
                        "'source_types' debe ser una lista no vacía con valores en "
                        f"{list(_SOURCE_TYPE_VALUES)}"
                    )
                }
            collected: list[SourceType] = []
            for raw in source_types_raw:
                if not isinstance(raw, str) or raw not in _SOURCE_TYPE_VALUES:
                    return {
                        "error": (
                            f"'source_types' contiene un valor inválido: {raw!r}. "
                            f"Permitidos: {list(_SOURCE_TYPE_VALUES)}"
                        )
                    }
                collected.append(SourceType(raw))
            source_types = tuple(collected)

        top_k_raw = args.get("top_k", _RETRIEVE_DEFAULT_TOP_K)
        if (
            not isinstance(top_k_raw, int)
            or isinstance(top_k_raw, bool)
            or top_k_raw < _RETRIEVE_MIN_TOP_K
            or top_k_raw > _RETRIEVE_MAX_TOP_K
        ):
            return {
                "error": (
                    f"'top_k' debe ser entero entre {_RETRIEVE_MIN_TOP_K} y "
                    f"{_RETRIEVE_MAX_TOP_K}"
                )
            }

        query = VectorQuery(
            text=query_text.strip(),
            top_k=top_k_raw,
            source_types=source_types,
            impuesto=impuesto_eff,
            fecha_devengo=devengo,
            min_score=0.0,
        )
        try:
            matches = retriever.search(query)
        except Exception as exc:  # noqa: BLE001 — superficie hacia el LLM
            return {"error": f"Fallo del retriever: {exc}"}

        if not matches:
            return {
                "count": 0,
                "sources": [],
                "rendered_context": "",
                "filters": {
                    "impuesto": impuesto_eff,
                    "devengo_date": devengo.isoformat() if devengo else None,
                    "source_types": (
                        [st.value for st in source_types] if source_types else None
                    ),
                    "top_k": top_k_raw,
                },
            }

        context = build_llm_context(matches, max_sources=top_k_raw)
        sources_payload: list[dict[str, Any]] = []
        for source, match in zip(context.sources, matches, strict=False):
            sources_payload.append(
                {
                    "index": source.index,
                    "chunk_id": source.chunk_id,
                    "source_type": source.source_type.value,
                    "header": source.header,
                    "metadata_lines": list(source.metadata_lines),
                    "body": source.body,
                    "rendered": source.render(),
                    "score": match.score,
                    "citation_hint": _citation_hint(match),
                }
            )
        return {
            "count": len(sources_payload),
            "sources": sources_payload,
            "rendered_context": context.rendered,
            "filters": {
                "impuesto": impuesto_eff,
                "devengo_date": devengo.isoformat() if devengo else None,
                "source_types": (
                    [st.value for st in source_types] if source_types else None
                ),
                "top_k": top_k_raw,
            },
        }

    return handler


_FISCAL_CALENDAR_MIN_WINDOW = 1
_FISCAL_CALENDAR_MAX_WINDOW = 365
_FISCAL_CALENDAR_DEFAULT_WINDOW = 90
_SEGMENT_VALUES = tuple(s.value for s in TaxpayerSegment)


def _make_get_fiscal_calendar() -> ToolHandler:
    def handler(args: dict[str, Any]) -> dict[str, Any]:
        today_raw = args.get("today_override")
        today: date | None = None
        if today_raw is not None:
            if not isinstance(today_raw, str) or not today_raw:
                return {
                    "error": "'today_override' debe ser string ISO 8601 (YYYY-MM-DD)"
                }
            try:
                today = date.fromisoformat(today_raw)
            except ValueError:
                return {
                    "error": "'today_override' inválida; usa formato ISO 8601 (YYYY-MM-DD)"
                }
        if today is None:
            today = date.today()

        window_raw = args.get("window_days", _FISCAL_CALENDAR_DEFAULT_WINDOW)
        if (
            not isinstance(window_raw, int)
            or isinstance(window_raw, bool)
            or window_raw < _FISCAL_CALENDAR_MIN_WINDOW
            or window_raw > _FISCAL_CALENDAR_MAX_WINDOW
        ):
            return {
                "error": (
                    f"'window_days' debe ser entero entre "
                    f"{_FISCAL_CALENDAR_MIN_WINDOW} y "
                    f"{_FISCAL_CALENDAR_MAX_WINDOW}"
                )
            }

        segments_raw = args.get("segments")
        segments: tuple[TaxpayerSegment, ...] | None = None
        if segments_raw is not None:
            if not isinstance(segments_raw, list) or not segments_raw:
                return {
                    "error": (
                        "'segments' debe ser una lista no vacía con valores "
                        f"en {list(_SEGMENT_VALUES)}"
                    )
                }
            parsed: list[TaxpayerSegment] = []
            for raw in segments_raw:
                if not isinstance(raw, str) or raw not in _SEGMENT_VALUES:
                    return {
                        "error": (
                            f"'segments' contiene un valor inválido: {raw!r}. "
                            f"Permitidos: {list(_SEGMENT_VALUES)}"
                        )
                    }
                parsed.append(TaxpayerSegment(raw))
            segments = tuple(parsed)

        resolution = resolve_current_fiscal_year(today)
        events = get_upcoming_events(
            today, window_days=window_raw, segments=segments
        )
        return {
            "today": today.isoformat(),
            "fiscal_year_resolution": resolution.to_dict(),
            "upcoming_events": [e.to_dict() for e in events],
            "filters": {
                "window_days": window_raw,
                "segments": [s.value for s in segments] if segments else None,
            },
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
    retriever: LegalContextRetriever | None = None,
) -> ToolRegistry:
    """Construye el set de tools por defecto, cargando del disco si no se inyectan.

    Si se pasa `retriever`, se añade la tool `retrieve_legal_context`
    sobre las cinco deterministas: el LLM podrá pedir contexto RAG
    reactivo (con `[FUENTE N]` y `citation_hint`) durante el loop,
    complementando el pre-fetch que hace `run_chat` antes del primer
    turno. Sin retriever, la registry queda como hasta Fase 0 (cinco
    tools puramente locales sobre corpus + registry + escalas).
    """
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
    reg.register(
        Tool(
            name="get_fiscal_calendar",
            description=(
                "Devuelve las próximas obligaciones fiscales (modelos AEAT: "
                "100, 130, 131, 303, 390, 111, 115, 190, 202, 200, 347, 349, "
                "720, 721) dentro de una ventana temporal, junto con la "
                "resolución del ejercicio fiscal en curso (campaña de Renta "
                "abierta/cerrada, último ejercicio cerrado, recomendación "
                "para preguntas genéricas). LLÁMALA cuando el usuario pregunte "
                "sobre plazos, calendario, ejercicio aplicable o cuando no "
                "especifique el año fiscal y necesites saber cuál asumir."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "today_override": {
                        "type": "string",
                        "description": (
                            "Fecha ISO 8601 (YYYY-MM-DD) usada como 'hoy'. "
                            "Opcional: por defecto se usa la fecha actual "
                            "del sistema. Útil para simular escenarios."
                        ),
                    },
                    "window_days": {
                        "type": "integer",
                        "minimum": _FISCAL_CALENDAR_MIN_WINDOW,
                        "maximum": _FISCAL_CALENDAR_MAX_WINDOW,
                        "default": _FISCAL_CALENDAR_DEFAULT_WINDOW,
                        "description": (
                            "Días hacia adelante para listar obligaciones "
                            f"(entre {_FISCAL_CALENDAR_MIN_WINDOW} y "
                            f"{_FISCAL_CALENDAR_MAX_WINDOW}, default "
                            f"{_FISCAL_CALENDAR_DEFAULT_WINDOW})."
                        ),
                    },
                    "segments": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": list(_SEGMENT_VALUES),
                        },
                        "description": (
                            "Filtra por segmento de contribuyente "
                            "(particular, autonomo, autonomo_modulos, "
                            "empresa, gran_empresa). Omítelo para vista "
                            "panorámica."
                        ),
                    },
                },
            },
            handler=_make_get_fiscal_calendar(),
        )
    )
    if retriever is not None:
        reg.register(
            Tool(
                name="retrieve_legal_context",
                description=(
                    "Busca semánticamente en el corpus auditable (normativa BOE "
                    "consolidada, consultas DGT vinculantes, resoluciones TEAC, "
                    "jurisprudencia TS/AN/TSJ, manuales AEAT) y devuelve hasta "
                    "`top_k` FUENTES con sus pinpoints. ÚSALA SIEMPRE que vayas "
                    "a afirmar algo legal cuyo texto exacto no figura ya en el "
                    "contexto: no debes citar normativa, consultas ni "
                    "jurisprudencia sin haberlas recuperado antes con esta tool. "
                    "Cada FUENTE incluye `citation_hint` con la cita pinpoint "
                    "lista para copiar verbatim al cerrar la respuesta."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Consulta libre en español. Reformúlala para "
                                "optimizar el retrieval: añade términos legales "
                                "clave (ej. 'gastos defensa jurídica trabajo "
                                "rendimientos netos IRPF art 19') en lugar del "
                                "lenguaje coloquial del usuario."
                            ),
                        },
                        "impuesto": {
                            "type": "string",
                            "enum": [
                                "irpf",
                                "iva",
                                "is",
                                "irnr",
                                "isd",
                                "itp",
                                "ip",
                            ],
                            "description": (
                                "Filtra por figura tributaria. Omítelo si la "
                                "pregunta es transversal."
                            ),
                        },
                        "devengo_date": {
                            "type": "string",
                            "description": (
                                "Fecha ISO 8601 (YYYY-MM-DD) del hecho "
                                "imponible. Solo se devolverán fuentes "
                                "vigentes en esa fecha — pásala SIEMPRE que "
                                "exista, evita citar normativa derogada."
                            ),
                        },
                        "source_types": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": list(_SOURCE_TYPE_VALUES),
                            },
                            "description": (
                                "Restringe a familias concretas (ej. solo "
                                "'consulta_dgt' para doctrina vinculante). "
                                "Omítelo para buscar en todo el corpus."
                            ),
                        },
                        "top_k": {
                            "type": "integer",
                            "minimum": _RETRIEVE_MIN_TOP_K,
                            "maximum": _RETRIEVE_MAX_TOP_K,
                            "default": _RETRIEVE_DEFAULT_TOP_K,
                            "description": (
                                "Número máximo de fuentes a devolver "
                                f"(entre {_RETRIEVE_MIN_TOP_K} y "
                                f"{_RETRIEVE_MAX_TOP_K})."
                            ),
                        },
                    },
                    "required": ["query"],
                },
                handler=_make_retrieve_legal_context(retriever),
            )
        )
    return reg


def serialize_tool_result(payload: dict[str, Any]) -> str:
    """Serialización canónica para devolver el resultado al LLM como tool_result."""
    return json.dumps(payload, ensure_ascii=False, default=str)
