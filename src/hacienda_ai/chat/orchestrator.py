"""Loop conversacional: alterna LLM ↔ tools y aplica el guard de citas.

Flujo de un turno de chat:

1. Construye el historial `[...prev, {role: user, content: message}]`.
2. Llama al LLM con `tools=tool_specs`.
3. Si la respuesta contiene `tool_use`, ejecuta cada tool localmente, mete
   los resultados como `tool_result` y vuelve a 2.
4. Cuando el LLM emite SOLO texto (sin tool_use): es el turno final.
5. Aplica `safety.verify_citations` al texto del turno final. Si veredicto
   = `block`, sustituye la respuesta por `SAFE_FALLBACK_MESSAGE`.
6. Devuelve el `ChatResult` con historial actualizado + texto + traza
   de las tools invocadas + veredicto del guard.

Hay un techo de `MAX_ITERATIONS` para que un bug de prompt o un LLM en
bucle no consuma tokens indefinidamente.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from ..irpf.scales import TaxScale
from ..models import Deduction, NormaRegistry
from ..safety import CitationCheckResult, verify_citations
from .client import LLMClient
from .tools import ToolRegistry, serialize_tool_result

MAX_ITERATIONS = 6

SAFE_FALLBACK_MESSAGE = (
    "Lo siento, no puedo devolver la respuesta que he generado porque "
    "contiene una cita normativa que no he podido verificar contra el "
    "corpus auditable. Reformula la pregunta o pídeme el dato concreto "
    "que necesitas y volveré a intentarlo con citas verificadas."
)


@dataclass
class ChatResult:
    history: list[dict[str, Any]]
    assistant_text: str
    tool_invocations: list[dict[str, Any]]
    citation_check: CitationCheckResult | None
    blocked_text: str | None
    iterations: int
    stop_reason: str | None

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
    """
    messages: list[dict[str, Any]] = list(history or [])
    messages.append({"role": "user", "content": user_message})
    tool_invocations: list[dict[str, Any]] = []
    stop_reason: str | None = None

    iterations = 0
    while iterations < max_iterations:
        iterations += 1
        turn = llm.next_turn(
            system=system_prompt,
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
    )


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
