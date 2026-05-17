"""Ingesta de consultas vinculantes de la Dirección General de Tributos.

La DGT publica sus consultas en el portal Petete del Ministerio de
Hacienda (https://petete.tributos.hacienda.gob.es). NO hay API REST
oficial, igual que CENDOJ: el acceso programático se hace contra el
buscador web. Por ello el módulo expone:

- `LocalDgtClient`: lee desde directorio local. Útil para CI, fixtures,
  y para operadores que archiven lotes descargados.
- `HttpDgtClient`: cliente experimental contra el buscador Petete con
  rate-limit conservador, User-Agent identificativo, y documentación
  clara sobre su carácter experimental.

El resto del pipeline (parser, extractor de criterio, persistencia,
runner) es agnóstico del cliente. El criterio extraído es heurístico y
queda marcado con `criterio_confidence=AUTO`; un revisor humano lo
promociona a `MANUAL` cuando lo valida.

Diferencias respecto al pipeline CENDOJ:

- Identificador: número `V<NNNN>-<YY>`, no ECLI.
- Sin filtro fiscal: TODA consulta DGT es por definición tributaria; sí
  detectamos el `Impuesto` principal para indexar.
- Sin fallo: no es órgano jurisdiccional; sí "criterio" (la conclusión
  doctrinal de la respuesta).
- Las consultas DGT son vinculantes para la AEAT (art. 89 LGT) pero
  NO para los tribunales — el LLM debe distinguir al citar.
"""

from __future__ import annotations

from .client import (
    DgtClient,
    DgtFetchError,
    HttpDgtClient,
    LocalDgtClient,
)
from .extractors import (
    detect_impuesto,
    extract_criterio,
    extract_normativa,
)
from .numero import (
    NumeroConsulta,
    NumeroConsultaParseError,
    parse_numero_consulta,
)
from .parser import (
    ConsultaParseError,
    ParsedConsulta,
    parse_consulta_html,
)
from .persistence import (
    PersistedConsulta,
    consulta_path,
    load_consulta,
    persist_consulta,
)
from .runner import (
    ConsultaOutcome,
    IngestionReport,
    impuesto_breakdown,
    run_ingest_for_numeros,
)

__all__ = [
    "ConsultaOutcome",
    "ConsultaParseError",
    "DgtClient",
    "DgtFetchError",
    "HttpDgtClient",
    "IngestionReport",
    "LocalDgtClient",
    "NumeroConsulta",
    "NumeroConsultaParseError",
    "ParsedConsulta",
    "PersistedConsulta",
    "consulta_path",
    "detect_impuesto",
    "extract_criterio",
    "extract_normativa",
    "impuesto_breakdown",
    "load_consulta",
    "parse_consulta_html",
    "parse_numero_consulta",
    "persist_consulta",
    "run_ingest_for_numeros",
]
