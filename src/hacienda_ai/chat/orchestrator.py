"""Loop conversacional: alterna LLM ↔ tools y aplica el guard de citas.

Flujo de un turno de chat:

0. (Opcional) Si se inyecta un `retriever`, se ejecuta una búsqueda
   híbrida con el mensaje del usuario + `devengo` + `impuesto` y el
   contexto recuperado se prepara con `build_llm_context` y se anexa al
   `system_prompt` efectivo de ESTA invocación. El system base
   (`prompts.SYSTEM_PROMPT`) no se muta y el historial guardado no
   contamina con el contexto: vive solo durante el loop.
1. Construye el historial `[...prev, {role: user, content: message}]`.
2. Llama al LLM con `system=system_efectivo`, `tools=tool_specs`.
3. Si la respuesta contiene `tool_use`, ejecuta cada tool localmente, mete
   los resultados como `tool_result` y vuelve a 2.
4. Cuando el LLM emite SOLO texto (sin tool_use): es el turno final.
5. Aplica `safety.verify_citations` al texto del turno final. Si veredicto
   = `block`, sustituye la respuesta por `SAFE_FALLBACK_MESSAGE`.
6. Devuelve el `ChatResult` con historial actualizado + texto + traza
   de las tools invocadas + veredicto del guard + ids de chunks RAG
   recuperados (auditoría).

Hay un techo de `MAX_ITERATIONS` para que un bug de prompt o un LLM en
bucle no consuma tokens indefinidamente.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from ..irpf.scales import TaxScale
from ..models import Deduction, NormaRegistry
from ..rag.grounding import build_llm_context
from ..rag.vector import VectorMatch, VectorQuery
from ..safety import CitationCheckResult, verify_citations
from .client import LLMClient
from .tools import ToolRegistry, serialize_tool_result

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 6
RAG_DEFAULT_TOP_K = 8


class LegalContextRetriever(Protocol):
    """Contrato mínimo del retriever híbrido para el orquestador.

    Cumplido por `rag.hybrid.HybridRetriever`. En tests se inyecta un
    stub determinista. Mantener este Protocol aquí (y no importar
    `HybridRetriever` directamente) desacopla la capa de chat del
    backend de retrieval y permite stubs sin construir BM25/Qdrant/
    embeddings reales.
    """

    def search(self, query: VectorQuery) -> list[VectorMatch]: ...


SAFE_FALLBACK_MESSAGE = (
    "Lo siento, no puedo devolver la respuesta que he generado porque "
    "contiene una cita normativa que no he podido verificar contra el "
    "corpus auditable. Reformula la pregunta o pídeme el dato concreto "
    "que necesitas y volveré a intentarlo con citas verificadas."
)

RAG_CONTEXT_INTRO = (
    "=== CONTEXTO LEGAL RECUPERADO ===\n"
    "Las FUENTES a continuación proceden del corpus auditable (normativa "
    "BOE, consultas DGT, resoluciones TEAC, jurisprudencia y manuales "
    "AEAT) y han sido preseleccionadas por relevancia para la consulta. "
    "Úsalas como base de tu razonamiento y cita cada afirmación legal "
    "con su ordinal [FUENTE N] y, cuando aplique, con el pinpoint "
    "canónico (BOE-A, art., V0123-24, ECLI, etc.). Si ninguna FUENTE "
    "cubre la pregunta, dilo explícitamente: no inventes citas fuera "
    "de este contexto."
)
RAG_CONTEXT_OUTRO = "=== FIN CONTEXTO LEGAL ==="


@dataclass
class ChatResult:
    history: list[dict[str, Any]]
    assistant_text: str
    tool_invocations: list[dict[str, Any]]
    citation_check: CitationCheckResult | None
    blocked_text: str | None
    iterations: int
    stop_reason: str | None
    retrieved_chunk_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        guard: dict[str, Any] | None = None
        if self.citation_check is not None:
            guard = {
                "verdict": self.citation_check.verdict,
                "blocking_issues": [
                    {"code": i.code, "message": i.message}
                    for i in self.citation_check.blocking_issues
                ],
                "warnings": [
                    {"code": i.code, "message": i.message}
                    for i in self.citation_check.warnings
                ],
            }
        return {
            "assistant": self.assistant_text,
            "blocked_text": self.blocked_text,
            "tool_invocations": self.tool_invocations,
            "citation_check": guard,
            "iterations": self.iterations,
            "stop_reason": self.stop_reason,
            "history": self.history,
            "retrieved_chunk_ids": list(self.retrieved_chunk_ids),
        }


def _final_text(blocks: list[dict[str, Any]]) -> str:
    return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()


def run_chat(
    *,
    user_message: str,
    history: list[dict[str, Any]] | None,
    llm: LLMClient,
    tools: ToolRegistry,
    system_prompt: str,
    devengo: date | None = None,
    max_iterations: int = MAX_ITERATIONS,
    corpus: list[Deduction] | None = None,
    registry: NormaRegistry | None = None,
    scales: list[TaxScale] | None = None,
    retriever: LegalContextRetriever | None = None,
    impuesto: str | None = None,
    rag_top_k: int = RAG_DEFAULT_TOP_K,
    rag_min_score: float = 0.0,
) -> ChatResult:
    """Ejecuta un turno de chat completo (potencialmente con varias llamadas
    al LLM si el modelo encadena tool_use).

    `devengo` se reenvía al guard de citas: una respuesta cita una norma
    derogada para esa fecha → block.

    `corpus`, `registry` y `scales` se pasan al guard final para detectar
    artículos no documentados (`ARTICLE_NOT_IN_CORPUS`) — sin ellos, el
    guard solo puede aplicar las reglas estructurales (año fuera de
    rango, alias sin mapeo) y dejará pasar como `warn` lo que sería un
    `block` real. Si el llamante construyó la `ToolRegistry` con
    `build_default_registry()`, debe pasar aquí las mismas listas.

    `retriever` activa el cableado RAG: si se inyecta, antes de la
    primera llamada al LLM se ejecuta una búsqueda híbrida con
    `(user_message, devengo, impuesto)` y los chunks recuperados se
    anexan al system prompt efectivo de ESTA invocación (envueltos por
    `RAG_CONTEXT_INTRO`/`RAG_CONTEXT_OUTRO`). Sin retriever, el
    comportamiento es idéntico al previo a Fase 1. `impuesto` filtra
    por figura tributaria del corpus (`irpf`, `iva`, `is`, …); cuando
    es `None` se acepta cualquier impuesto. Si la búsqueda falla
    (red caída, dependencias del backend) el chat continúa sin
    contexto y se registra un warning — el RAG es complementario, no
    bloqueante.
    """
    messages: list[dict[str, Any]] = list(history or [])
    messages.append({"role": "user", "content": user_message})
    tool_invocations: list[dict[str, Any]] = []
    stop_reason: str | None = None

    effective_system, retrieved_chunk_ids = _augment_system_with_rag(
        system_prompt=system_prompt,
        retriever=retriever,
        query_text=user_message,
        devengo=devengo,
        impuesto=impuesto,
        top_k=rag_top_k,
        min_score=rag_min_score,
    )

    iterations = 0
    while iterations < max_iterations:
        iterations += 1
        turn = llm.next_turn(
            system=effective_system,
            messages=messages,
            tools=tools.specs,
        )
        stop_reason = turn.stop_reason
        blocks = list(turn.content_blocks)
        if not blocks:
            # LLM se calló: cerramos con texto vacío y guard sobre cadena vacía.
            assistant_text = ""
            messages.append({"role": "assistant", "content": []})
            break

        messages.append({"role": "assistant", "content": blocks})

        tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
        if not tool_uses:
            assistant_text = _final_text(blocks)
            break

        tool_results: list[dict[str, Any]] = []
        for tu in tool_uses:
            name = tu["name"]
            tool_input = tu.get("input") or {}
            result = tools.dispatch(name, tool_input)
            tool_invocations.append(
                {
                    "iteration": iterations,
                    "tool": name,
                    "input": tool_input,
                    "result_preview": _preview(result),
                }
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu["id"],
                    "content": serialize_tool_result(result),
                }
            )
        messages.append({"role": "user", "content": tool_results})
    else:  # max iterations agotadas
        assistant_text = (
            "Se ha alcanzado el límite de iteraciones del orquestador "
            "sin obtener una respuesta final. Reformula la pregunta o "
            "redúcela a un único cálculo."
        )

    guard = verify_citations(
        assistant_text,
        corpus=corpus,
        scales=scales,
        registry=registry,
        devengo=devengo,
    )
    blocked_original: str | None = None
    if guard.verdict == "block":
        blocked_original = assistant_text
        assistant_text = SAFE_FALLBACK_MESSAGE
        # En el historial mantenemos el bloque del modelo intacto + un
        # turno extra del asistente con el mensaje seguro. Quien lea el
        # historial verá ambos: el original (bloqueado) y la respuesta
        # que el usuario realmente recibió.
        messages.append(
            {"role": "assistant", "content": [{"type": "text", "text": assistant_text}]}
        )

    return ChatResult(
        history=messages,
        assistant_text=assistant_text,
        tool_invocations=tool_invocations,
        citation_check=guard,
        blocked_text=blocked_original,
        iterations=iterations,
        stop_reason=stop_reason,
        retrieved_chunk_ids=retrieved_chunk_ids,
    )


def _augment_system_with_rag(
    *,
    system_prompt: str,
    retriever: LegalContextRetriever | None,
    query_text: str,
    devengo: date | None,
    impuesto: str | None,
    top_k: int,
    min_score: float,
) -> tuple[str, list[str]]:
    """Ejecuta el retrieval híbrido y construye el system efectivo.

    Devuelve `(system_efectivo, chunk_ids_recuperados)`. Si `retriever`
    es None, o la búsqueda devuelve vacío, o el backend lanza, se
    devuelve `(system_prompt, [])` sin lanzar excepción — el RAG es
    una capa complementaria y nunca debe tirar el chat.

    El system base (`prompts.SYSTEM_PROMPT`) NO se muta; el contexto
    se anexa en una copia local que vive solo en esta invocación. El
    historial guardado tampoco se contamina: ahí queda el mensaje del
    usuario tal cual lo escribió.
    """
    if retriever is None:
        return system_prompt, []
    query = VectorQuery(
        text=query_text,
        top_k=top_k,
        impuesto=impuesto,
        fecha_devengo=devengo,
        min_score=min_score,
    )
    try:
        matches = retriever.search(query)
    except Exception as exc:  # noqa: BLE001 — defensa en profundidad
        logger.warning(
            "RAG retrieval falló (%s). Continuando sin contexto recuperado.",
            exc,
        )
        return system_prompt, []
    if not matches:
        return system_prompt, []
    context = build_llm_context(matches, max_sources=top_k)
    if not context.sources:
        return system_prompt, []
    chunk_ids = [s.chunk_id for s in context.sources]
    extended = (
        system_prompt
        + "\n\n"
        + RAG_CONTEXT_INTRO
        + "\n\n"
        + context.rendered
        + "\n\n"
        + RAG_CONTEXT_OUTRO
    )
    return extended, chunk_ids


def _preview(result: dict[str, Any]) -> dict[str, Any]:
    """Resumen acotado del resultado de una tool para no inflar la respuesta.

    Conserva las claves de primer nivel y trunca listas largas a 5 elementos.
    El payload completo viaja al LLM dentro del historial; este preview es
    solo para la traza que devolvemos al cliente HTTP.
    """
    preview: dict[str, Any] = {}
    for key, value in result.items():
        if isinstance(value, list):
            preview[key] = {
                "count": len(value),
                "head": value[:5],
            }
        elif isinstance(value, dict) and len(value) > 8:
            preview[key] = {k: value[k] for k in list(value)[:8]} | {"_truncated": True}
        else:
            preview[key] = value
    return preview
