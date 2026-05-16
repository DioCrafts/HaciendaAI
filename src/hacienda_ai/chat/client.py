"""Cliente LLM con tool use.

Abstracción `LLMClient` que el orquestador usa para hablar con un modelo
sin acoplarse al SDK concreto. Dos implementaciones:

- `AnthropicClient`: usa el SDK oficial `anthropic`. Requiere
  `ANTHROPIC_API_KEY` en el entorno. Si la dependencia no está
  instalada, su constructor lanza `LLMUnavailable`; el endpoint /chat
  lo captura y devuelve 503 con mensaje claro en lugar de crashear.
- `FakeLLMClient`: guion determinista que reproduce un flujo de
  `tool_use` predefinido. Pensado para tests de orquestación sin red.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

DEFAULT_MODEL = "claude-sonnet-4-5"
DEFAULT_MAX_TOKENS = 1024


class LLMUnavailable(RuntimeError):
    """Se lanza cuando no se puede inicializar un cliente LLM real."""


@dataclass(frozen=True)
class LLMTurn:
    """Un turno del LLM normalizado al formato de bloques de Anthropic.

    `content_blocks` es una lista con bloques de tipo `text` o `tool_use`,
    cada uno con sus claves originales:

    - text: `{"type": "text", "text": "..."}`
    - tool_use: `{"type": "tool_use", "id": "tu_xxx", "name": "...", "input": {...}}`

    `stop_reason` proviene directamente del API (`end_turn`, `tool_use`,
    `max_tokens`, etc.).
    """

    content_blocks: list[dict[str, Any]]
    stop_reason: str | None


class LLMClient(Protocol):
    def next_turn(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMTurn:
        """Una sola llamada al LLM. El orquestador externo gestiona el loop."""
        ...


# ---------- Cliente real ----------


class AnthropicClient:
    """Cliente sobre el SDK oficial `anthropic`.

    Mantiene la conexión con el modelo y traduce entre el formato neutro
    `LLMTurn` y los objetos del SDK. La iteración de tool_use vive en el
    `orchestrator`: este cliente solo hace una llamada por invocación.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover — entorno sin SDK
            raise LLMUnavailable(
                "El SDK `anthropic` no está instalado. "
                "Instala el extra api o `pip install anthropic`."
            ) from exc
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise LLMUnavailable(
                "Falta ANTHROPIC_API_KEY: el cliente real no puede inicializarse."
            )
        self._client = Anthropic(api_key=key)
        self._model = model
        self._max_tokens = max_tokens

    def next_turn(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMTurn:
        # El SDK pide TypedDicts muy detallados para `tools` y `messages`;
        # nuestro modelo neutro `dict[str, Any]` cumple en runtime pero
        # mypy no lo acepta. Casteamos en el punto de salida.
        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            tools=tools,  # type: ignore[arg-type]
            messages=messages,  # type: ignore[arg-type]
        )
        blocks: list[dict[str, Any]] = []
        for block in response.content:
            # El SDK tipa `block` como Union amplia (TextBlock | ToolUseBlock
            # | ThinkingBlock | ...); mypy no puede narrowear por el `type`
            # string, así que validamos a mano y silenciamos los accesos
            # tras la rama. Los demás bloques (thinking, etc.) se ignoran:
            # el orquestador solo razona sobre text y tool_use.
            kind = getattr(block, "type", None)
            if kind == "text":
                blocks.append({"type": "text", "text": block.text})  # type: ignore[union-attr]
            elif kind == "tool_use":
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": block.id,  # type: ignore[union-attr]
                        "name": block.name,  # type: ignore[union-attr]
                        "input": dict(block.input),  # type: ignore[union-attr]
                    }
                )
        return LLMTurn(
            content_blocks=blocks,
            stop_reason=response.stop_reason,
        )


# ---------- Cliente fake para tests ----------


@dataclass
class FakeTurn:
    """Especificación de un turno simulado del LLM.

    - `text`: bloque de texto final (opcional).
    - `tool_calls`: tuplas (tool_name, input_dict) que el LLM "pide".
    - `stop_reason`: si se omite, se deriva: `tool_use` cuando hay
      llamadas a tools, `end_turn` cuando solo hay texto.
    """

    text: str | None = None
    tool_calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    stop_reason: str | None = None

    def materialize(self, base_id: int) -> LLMTurn:
        blocks: list[dict[str, Any]] = []
        if self.text:
            blocks.append({"type": "text", "text": self.text})
        for idx, (name, payload) in enumerate(self.tool_calls):
            blocks.append(
                {
                    "type": "tool_use",
                    "id": f"toolu_fake_{base_id}_{idx}",
                    "name": name,
                    "input": payload,
                }
            )
        derived = (
            self.stop_reason
            or ("tool_use" if self.tool_calls else "end_turn")
        )
        return LLMTurn(content_blocks=blocks, stop_reason=derived)


class FakeLLMClient:
    """LLM cliente determinista. Reproduce el guion `script` paso a paso.

    Cada llamada a `next_turn` consume el siguiente `FakeTurn` del guion.
    Si se agota antes de que el orquestador termine, devuelve un turno
    `end_turn` vacío (provoca cierre del loop).
    """

    def __init__(self, script: Iterable[FakeTurn]) -> None:
        self._script: list[FakeTurn] = list(script)
        self._idx = 0
        self.calls: list[dict[str, Any]] = []

    def next_turn(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMTurn:
        self.calls.append(
            {
                "system_len": len(system),
                "messages": list(messages),
                "tool_names": [t["name"] for t in tools],
            }
        )
        if self._idx >= len(self._script):
            return LLMTurn(content_blocks=[], stop_reason="end_turn")
        turn = self._script[self._idx]
        self._idx += 1
        return turn.materialize(self._idx)
