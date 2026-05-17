"""Paquete `chat`: orquestador LLM con tool use estricto y guard de citas.

Tres responsabilidades:

- `tools`: definiciones de las herramientas que el LLM puede invocar
  (`get_deduction_catalog`, `evaluate_profile`, `compute_irpf_quota`,
  `verify_citation`, `search_norma`). Cada una está implementada en Python
  puro y devuelve un payload JSON-serializable; el LLM nunca calcula
  importes por su cuenta.
- `client`: abstracción `LLMClient` (Protocol) + dos implementaciones:
  `AnthropicClient` (real, usa el SDK oficial si está disponible y
  `ANTHROPIC_API_KEY` está fijada) y `FakeLLMClient` (determinista,
  usado en tests sin red).
- `orchestrator`: loop conversacional que alterna mensajes con el LLM y
  resultados de tools hasta que el modelo emite una respuesta final.
  Antes de devolverla al usuario, la respuesta pasa por
  `safety.verify_citations`: si el guard la marca como `block`, se
  sustituye por un mensaje seguro pidiendo reformular.
"""

from .client import (
    AnthropicClient,
    FakeLLMClient,
    FakeTurn,
    LLMClient,
    LLMTurn,
    LLMUnavailable,
)
from .orchestrator import (
    MAX_ITERATIONS,
    RAG_CONTEXT_INTRO,
    RAG_CONTEXT_OUTRO,
    RAG_DEFAULT_TOP_K,
    SAFE_FALLBACK_MESSAGE,
    ChatResult,
    LegalContextRetriever,
    run_chat,
)
from .prompts import SYSTEM_PROMPT
from .tools import ToolRegistry, build_default_registry

__all__ = [
    "AnthropicClient",
    "ChatResult",
    "FakeLLMClient",
    "FakeTurn",
    "LLMClient",
    "LLMTurn",
    "LLMUnavailable",
    "LegalContextRetriever",
    "MAX_ITERATIONS",
    "RAG_CONTEXT_INTRO",
    "RAG_CONTEXT_OUTRO",
    "RAG_DEFAULT_TOP_K",
    "SAFE_FALLBACK_MESSAGE",
    "SYSTEM_PROMPT",
    "ToolRegistry",
    "build_default_registry",
    "run_chat",
]
