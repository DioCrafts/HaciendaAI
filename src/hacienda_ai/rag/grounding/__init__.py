"""Citation grounding: el LLM cita SOLO chunks recuperados.

Esta capa cierra el ciclo del RAG:

1. **`context_builder`**: convierte la lista de `VectorMatch` del
   retrieval en un texto de contexto para el LLM. Cada chunk se
   numera como `[FUENTE 1]`, `[FUENTE 2]`... con metadata pinpoint
   visible (boe_id, artículo, apartado, vigencia, órgano…). El LLM
   se instruye en el system prompt a citar `[FUENTE N]` y a NO
   inventar fuentes fuera del contexto.

2. **`citation_validator`**: tras recibir la respuesta del LLM,
   verifica:
   - Que cada referencia `[FUENTE N]` apunta a un chunk del contexto.
   - Que cada cita normativa (BOE-A, art. X, V0123-24…) que aparece
     en la respuesta corresponde a un chunk recuperado o, en su
     defecto, está marcada por `citation_guard` como `safe`.
   - Que no se cita ninguna norma derogada en la fecha del devengo
     (cruce con el `TemporalFilterReport`).

   Devuelve un `GroundingVerdict` con `safe`/`warn`/`block` y lista
   de problemas. El orchestrator del chat decide qué hacer
   (`block` → reescribir respuesta).

Esta capa es complementaria a `citation_guard.py` (que valida citas
contra el corpus auditable). Aquí restringimos las citas a SÓLO el
contexto entregado al LLM — más estricto, más útil para reducir
alucinaciones en respuestas generativas.
"""

from __future__ import annotations

from .citation_validator import (
    CitationIssue,
    GroundingVerdict,
    GroundingVerdictLevel,
    validate_grounded_response,
)
from .context_builder import (
    BuiltContext,
    ContextSource,
    build_llm_context,
    format_metadata_for_llm,
)

__all__ = [
    "BuiltContext",
    "CitationIssue",
    "ContextSource",
    "GroundingVerdict",
    "GroundingVerdictLevel",
    "build_llm_context",
    "format_metadata_for_llm",
    "validate_grounded_response",
]
