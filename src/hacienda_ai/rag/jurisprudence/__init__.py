"""Ingesta de jurisprudencia tributaria desde CENDOJ (CGPJ).

CENDOJ (Centro de Documentación Judicial del CGPJ) es el repositorio
oficial de jurisprudencia española. NO expone API REST oficial; sí un
buscador web. Este paquete asume esa limitación y por ello expone:

- **`LocalCendojClient`**: lee sentencias desde un directorio local
  (útil para CI, tests, y para operadores que descarguen lotes vía el
  buscador web del CGPJ y los archiven en disco).
- **`HttpCendojClient`**: cliente experimental contra el buscador
  público del CGPJ. Rate-limit muy conservador (1 req/3 s),
  User-Agent identificativo, sin scraping masivo. Documentado como
  experimental porque el HTML del buscador puede cambiar.

El resto del pipeline (parser, filtro fiscal, extractores de fallo y
ratio decidendi, persistencia, runner) es agnóstico del cliente. Cuando
exista una vía oficial alternativa (volcado del CGPJ, dataset abierto)
basta con añadir otra implementación del Protocol `CendojClient`.

Política operacional: la jurisprudencia se publica con cadencia
semanal típica, así que el workflow de ingesta corre semanalmente y
abre PR con las nuevas sentencias para revisión humana (mismo patrón
que ingest-boe).

Modelo de dominio en `models/sentencia.py`. Estructura del corpus en
`data/jurisprudencia/<organo>/<año>/<ECLI>.json`.
"""

from __future__ import annotations

from .client import (
    CendojClient,
    CendojFetchError,
    CendojSearchResult,
    HttpCendojClient,
    LocalCendojClient,
)
from .ecli import ECLI, EcliParseError, organo_from_tribunal_codigo, parse_ecli
from .extractors import (
    extract_fallo,
    extract_ratio_decidendi,
)
from .parser import (
    ParsedSentencia,
    SentenciaParseError,
    parse_sentencia_html,
)
from .persistence import (
    PersistedSentencia,
    load_sentencia,
    persist_sentencia,
    sentencia_path,
)
from .runner import (
    IngestionReport,
    SentenciaOutcome,
    run_ingest_for_eclis,
)
from .tax_filter import (
    TaxClassification,
    classify_sentencia,
)

__all__ = [
    "CendojClient",
    "CendojFetchError",
    "CendojSearchResult",
    "ECLI",
    "EcliParseError",
    "HttpCendojClient",
    "IngestionReport",
    "LocalCendojClient",
    "ParsedSentencia",
    "PersistedSentencia",
    "SentenciaOutcome",
    "SentenciaParseError",
    "TaxClassification",
    "classify_sentencia",
    "extract_fallo",
    "extract_ratio_decidendi",
    "load_sentencia",
    "organo_from_tribunal_codigo",
    "parse_ecli",
    "parse_sentencia_html",
    "persist_sentencia",
    "run_ingest_for_eclis",
    "sentencia_path",
]
