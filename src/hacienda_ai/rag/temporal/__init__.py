"""Filtro temporal duro para retrieval RAG.

La normativa fiscal cambia constantemente. Una respuesta del LLM sobre
"el límite de gastos de defensa jurídica" es muy distinta si se aplica
al ejercicio 2014 (vigencia original) o 2024 (tras reforma Ley
26/2014). Sin filtro temporal el retrieval puede devolver chunks de
versiones de la norma que NO estaban vigentes en la fecha del hecho
imponible, y el LLM las citará como si fueran derecho aplicable.

Política "filtro temporal duro":

1. Toda consulta al retrieval DEBE llevar `fecha_devengo`. Si falta,
   el guard de `enforce_temporal_filter` lo detecta y o bien lanza
   (modo `strict`) o asume `date.today()` con WARNING en logs (modo
   `warn`).

2. Chunks sin `effective_from` en metadata se consideran "atemporales"
   (manuales sin fecha, FAQs INFORMA). En modo `strict` los excluimos:
   si no podemos verificar la vigencia, no pasan. En modo `warn` se
   aceptan con disclaimer en la metadata del match.

3. Chunks con `effective_from > fecha_devengo` (norma posterior al
   hecho) o con `effective_to < fecha_devengo` (norma derogada en la
   fecha) NO pasan, en ningún modo.

Esta política es defensa en profundidad: el `VectorStore` ya aplica
el filtro a nivel SQL/Qdrant, pero `enforce_temporal_filter` valida
post-search y deja constancia del modo aplicado para el citation
guard. Sin esto, una metadata mal escrita pasaría silenciosamente.
"""

from __future__ import annotations

from .filter import (
    StrictTemporalFilterError,
    TemporalEnforcementMode,
    TemporalFilterReport,
    enforce_temporal_filter,
    require_fecha_devengo,
)

__all__ = [
    "StrictTemporalFilterError",
    "TemporalEnforcementMode",
    "TemporalFilterReport",
    "enforce_temporal_filter",
    "require_fecha_devengo",
]
