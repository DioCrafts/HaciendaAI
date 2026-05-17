"""Ingesta de resoluciones de Tribunales Económico-Administrativos.

Cubre TEAC (central, vinculante para AEAT y TEAR cuando unifica
criterio), TEAR (regionales) y TEAL (locales). El portal oficial es el
buscador de doctrina y criterios del Ministerio de Hacienda
(https://serviciostelematicos.minhap.gob.es/DYCTEA/criterio/).

Mismo patrón que CENDOJ/DGT: no hay API REST oficial, así que el módulo
expone:

- `LocalTeacClient`: lee HTML desde directorio local. Útil para CI y
  para operadores que archiven lotes descargados manualmente.
- `HttpTeacClient`: cliente experimental contra el buscador con
  rate-limit conservador y User-Agent identificativo.

El resto del pipeline (parser, extractor de criterio, runner, persistencia)
es agnóstico del cliente.

Decisiones específicas TEAC vs los otros pipelines:

- **Tipo de resolución**: detector heurístico que distingue
  `UNIFICA_CRITERIO` (art. 242 LGT), `EXTIENDE_EFECTOS` (art. 244 LGT)
  y `ORDINARIA`. Crítico porque el LLM debe citar con peso doctrinal
  distinto.
- **No hay filtro fiscal**: todas las resoluciones TEAC/TEAR son
  tributarias por definición (es la vía económico-administrativa).
- **Identificación**: número de reclamación con formato `<NN>/<NNNNN>/<año>`
  más sufijos opcionales. Normalizamos a canónico.
"""

from __future__ import annotations

from .client import (
    HttpTeacClient,
    LocalTeacClient,
    TeacClient,
    TeacFetchError,
)
from .extractors import (
    detect_sentido,
    detect_tipo,
    extract_criterio,
    extract_normativa,
)
from .numero import (
    NumeroReclamacion,
    NumeroReclamacionParseError,
    parse_numero_reclamacion,
)
from .parser import (
    ParsedResolucion,
    ResolucionParseError,
    parse_resolucion_html,
)
from .persistence import (
    PersistedResolucion,
    consulta_path,
    load_resolucion,
    persist_resolucion,
)
from .runner import (
    IngestionReport,
    ResolucionOutcome,
    impuesto_breakdown,
    run_ingest_for_numeros,
    tipo_breakdown,
)

__all__ = [
    "HttpTeacClient",
    "IngestionReport",
    "LocalTeacClient",
    "NumeroReclamacion",
    "NumeroReclamacionParseError",
    "ParsedResolucion",
    "PersistedResolucion",
    "ResolucionOutcome",
    "ResolucionParseError",
    "TeacClient",
    "TeacFetchError",
    "consulta_path",
    "detect_sentido",
    "detect_tipo",
    "extract_criterio",
    "extract_normativa",
    "impuesto_breakdown",
    "load_resolucion",
    "parse_numero_reclamacion",
    "parse_resolucion_html",
    "persist_resolucion",
    "run_ingest_for_numeros",
    "tipo_breakdown",
]
