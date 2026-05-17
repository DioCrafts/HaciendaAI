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
4. Cuando el LLM emite SOLO texto (sin tool_use): se ejecuta
   `safety.verify_citations` sobre ese texto.
5. **Verify retry loop**: si el veredicto es `block`, el orquestador
   NO se rinde: inyecta un mensaje `user` con feedback estructurado
   (códigos de issue + instrucción "reescribe" + recordatorio de que
   puede llamar a `retrieve_legal_context`) y vuelve a 2, hasta agotar
   `MAX_VERIFY_RETRIES`. Si tras los reintentos sigue en `block`, se
   aplica el `SAFE_FALLBACK_MESSAGE` como antes; el texto original
   queda en `blocked_text` para auditoría. `verify_history` registra
   todos los veredictos por orden.
6. Devuelve el `ChatResult` con historial actualizado + texto + traza
   de las tools invocadas + último guard + ids de chunks RAG
   recuperados + `verify_attempts` + `verify_history`.

Hay dos topes:
- `MAX_ITERATIONS`: protección contra loops de tool_use sin fin.
- `MAX_VERIFY_RETRIES`: cuántas veces reformulamos la respuesta tras
  un `block` antes de caer al fallback. `0` reproduce el comportamiento
  legacy (fallback inmediato).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..irpf.scales import TaxScale
from ..models import Deduction, NormaRegistry, Source
from ..rag.grounding import build_llm_context
from ..rag.vector import VectorQuery
from ..safety import CitationCheckResult, verify_citations
from .client import LLMClient
from .retriever import LegalContextRetriever
from .tools import ToolRegistry, serialize_tool_result

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 6
MAX_VERIFY_RETRIES = 2
RAG_DEFAULT_TOP_K = 8


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
    verify_history: list[CitationCheckResult] = field(default_factory=list)

    @property
    def verify_attempts(self) -> int:
        """Cuántas verificaciones de citas se ejecutaron en este turno.

        Incluye la primera más cada reintento. Mismo valor que
        `len(verify_history)`. Expuesto como propiedad para no
        duplicar estado.
        """
        return len(self.verify_history)

    def to_dict(self) -> dict[str, Any]:
        guard: dict[str, Any] | None = None
        if self.citation_check is not None:
            guard = _serialize_check(self.citation_check)
        return {
            "assistant": self.assistant_text,
            "blocked_text": self.blocked_text,
            "tool_invocations": self.tool_invocations,
            "citation_check": guard,
            "iterations": self.iterations,
            "stop_reason": self.stop_reason,
            "history": self.history,
            "retrieved_chunk_ids": list(self.retrieved_chunk_ids),
            "verify_attempts": self.verify_attempts,
            "verify_history": [_serialize_check(c) for c in self.verify_history],
        }


def _serialize_check(check: CitationCheckResult) -> dict[str, Any]:
    return {
        "verdict": check.verdict,
        "blocking_issues": [
            {"code": i.code, "message": i.message} for i in check.blocking_issues
        ],
        "warnings": [
            {"code": i.code, "message": i.message} for i in check.warnings
        ],
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
    max_verify_retries: int = MAX_VERIFY_RETRIES,
    corpus: list[Deduction] | None = None,
    registry: NormaRegistry | None = None,
    scales: list[TaxScale] | None = None,
    retriever: LegalContextRetriever | None = None,
    impuesto: str | None = None,
    rag_top_k: int = RAG_DEFAULT_TOP_K,
    rag_min_score: float = 0.0,
    extra_documented_sources: list[Source] | None = None,
) -> ChatResult:
    """Ejecuta un turno de chat completo (potencialmente con varias llamadas
    al LLM si el modelo encadena tool_use y/o reformula tras un block).

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

    `max_verify_retries` controla cuántas veces se reformula la
    respuesta tras un `block` antes de caer al `SAFE_FALLBACK_MESSAGE`.
    Cada reintento consume una iteración del loop principal — el
    presupuesto compartido `max_iterations` protege ambos modos. Con
    `max_verify_retries=0` el comportamiento es legacy: el primer
    block dispara el fallback de inmediato.
    """
    if max_verify_retries < 0:
        raise ValueError("max_verify_retries debe ser >= 0")

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

    verify_history: list[CitationCheckResult] = []
    verify_retries_used = 0
    blocked_original: str | None = None
    assistant_text: str = ""
    last_guard: CitationCheckResult | None = None
    final_reached = False

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
            last_guard = verify_citations(
                assistant_text,
                corpus=corpus,
                scales=scales,
                registry=registry,
                devengo=devengo,
                extra_documented_sources=extra_documented_sources,
            )
            verify_history.append(last_guard)
            final_reached = True
            break

        messages.append({"role": "assistant", "content": blocks})

        tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
        if tool_uses:
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
            continue

        # Texto final del LLM: ejecutamos el guard.
        assistant_text = _final_text(blocks)
        last_guard = verify_citations(
            assistant_text,
            corpus=corpus,
            scales=scales,
            registry=registry,
            devengo=devengo,
            extra_documented_sources=extra_documented_sources,
        )
        verify_history.append(last_guard)

        if last_guard.verdict != "block":
            # safe o warn → aceptamos la respuesta.
            final_reached = True
            break

        # Block. ¿Quedan reintentos?
        if verify_retries_used >= max_verify_retries:
            blocked_original = assistant_text
            assistant_text = SAFE_FALLBACK_MESSAGE
            messages.append(
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": assistant_text}],
                }
            )
            final_reached = True
            break

        # Inyectamos feedback estructurado y dejamos que el modelo
        # reformule en la próxima iteración del while.
        verify_retries_used += 1
        feedback = _build_verify_feedback(
            last_guard,
            attempt=verify_retries_used,
            max_attempts=max_verify_retries,
        )
        messages.append({"role": "user", "content": feedback})

    if not final_reached:
        # Agotadas las iteraciones del loop sin cerrar — puede ocurrir
        # en un loop infinito de tool_use o en una cadena de reintentos
        # que no convergen. Sustituimos con el mensaje de límite y
        # ejecutamos el guard también sobre él (será siempre `safe`,
        # pero mantenemos la simetría: TODA respuesta al usuario pasa
        # por el guard al menos una vez).
        assistant_text = (
            "Se ha alcanzado el límite de iteraciones del orquestador "
            "sin obtener una respuesta final. Reformula la pregunta o "
            "redúcela a un único cálculo."
        )
        last_guard = verify_citations(
            assistant_text,
            corpus=corpus,
            scales=scales,
            registry=registry,
            devengo=devengo,
            extra_documented_sources=extra_documented_sources,
        )
        verify_history.append(last_guard)

    return ChatResult(
        history=messages,
        assistant_text=assistant_text,
        tool_invocations=tool_invocations,
        citation_check=last_guard,
        blocked_text=blocked_original,
        iterations=iterations,
        stop_reason=stop_reason,
        retrieved_chunk_ids=retrieved_chunk_ids,
        verify_history=verify_history,
    )


def _build_verify_feedback(
    check: CitationCheckResult,
    *,
    attempt: int,
    max_attempts: int,
) -> str:
    """Construye el mensaje user que se inyecta tras un veredicto `block`.

    El feedback es accionable: lista los códigos de issue concretos,
    instruye explícitamente a reescribir, recuerda que existe
    `retrieve_legal_context` para buscar las citas correctas, y avisa
    cuántos intentos quedan antes del fallback. La estructura sigue
    el patrón "ToolErrorFeedback" recomendado por Anthropic para que
    el modelo pueda corregir su salida en el siguiente turno.
    """
    issue_lines = [
        f"  - [{issue.code}] {issue.message}"
        for issue in check.blocking_issues
    ]
    issues_text = "\n".join(issue_lines) if issue_lines else "  (sin detalles)"
    remaining = max_attempts - attempt
    plural = "intento" if remaining == 1 else "intentos"
    return (
        "He ejecutado el verificador de citas sobre tu respuesta y ha "
        "detectado problemas que la invalidan:\n"
        f"{issues_text}\n\n"
        "Reescribe la respuesta eliminando o sustituyendo las citas "
        "problemáticas. Si necesitas el texto correcto de una norma, "
        "consulta DGT, resolución TEAC, sentencia o manual AEAT antes "
        "de afirmar, llama a `retrieve_legal_context` con una query "
        "reformulada y los filtros adecuados (impuesto, devengo_date) "
        "y construye la respuesta a partir de las FUENTES devueltas.\n"
        f"Te quedan {remaining} {plural} antes de que tu respuesta se "
        "sustituya por un mensaje de error genérico."
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
