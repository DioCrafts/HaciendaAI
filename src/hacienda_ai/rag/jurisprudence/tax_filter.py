"""Clasificador de materia tributaria sobre sentencias parseadas.

Decide si una sentencia entra al corpus fiscal. A diferencia del filtro
BOE, aquí la señal principal es la **materia** del litigio: una
sentencia de la Sala 3ª del TS puede ser tributaria (caso típico) o
sobre función pública, contratación, etc.

Señales utilizadas:
- Campo `Materia` de la cabecera (CENDOJ lo expone para muchas
  sentencias).
- Sala del órgano (`Sección` para TS): la Sala 3ª Sección 2ª del TS
  es la sala fiscal por excelencia, prácticamente todo lo que ahí se
  decide es tributario.
- Keywords en el resumen / texto: vocabulario tributario claro.

Política conservadora (alineada con el filtro BOE): ante la duda,
**incluir**. El revisor humano descarta los falsos positivos al
mergear el PR del cron de ingesta.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from .parser import ParsedSentencia

TaxRelevance = Literal["fiscal", "probable", "no_fiscal"]


@dataclass(frozen=True)
class TaxClassification:
    relevance: TaxRelevance
    matched_keywords: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def accept(self) -> bool:
        return self.relevance in ("fiscal", "probable")


# Patrones de "Materia" CENDOJ que son inequívocamente tributarias.
_MATERIA_FISCAL_PATTERNS = (
    "tributari",
    "fiscal",
    "impuesto",
    "irpf",
    "impuesto sobre la renta",
    "iva",
    "impuesto sobre el valor anadido",
    "impuesto sobre sociedades",
    "impuesto sobre el patrimonio",
    "sucesiones",
    "donaciones",
    "transmisiones patrimoniales",
    "actos juridicos documentados",
    "hacienda publica",
    "haciendas locales",
    "aeat",
    "tasa fiscal",
    "ibi",
    "iibtnu",
    "plusvalia",
    "recaudacion",
    "inspeccion tributaria",
    "infraccion tributaria",
    "sancion tributaria",
)

# Vocabulario tributario fuerte en el cuerpo (búsqueda con substring).
_BODY_KEYWORDS_FUERTES = (
    "impuesto sobre la renta de las personas fisicas",
    "impuesto sobre sociedades",
    "impuesto sobre el valor anadido",
    "impuesto sobre el patrimonio",
    "ley general tributaria",
    "agencia estatal de administracion tributaria",
    "direccion general de tributos",
    "tribunal economico administrativo",
    "obligacion tributaria",
    "deuda tributaria",
    "liquidacion tributaria",
    "comprobacion limitada",
    "regularizacion tributaria",
    "infraccion tributaria",
    "sancion tributaria",
    "rendimientos del trabajo",
    "rendimientos de actividades economicas",
    "ganancia patrimonial",
    "minimo personal y familiar",
)

# Acrónimos cortos: word-boundary obligatorio para evitar falsos
# positivos (`administrativa` no contiene `iva` como palabra).
_BODY_ACRONIMOS = (
    "irpf",
    "iva",
    "isd",
    "iibtnu",
    "ibi",
    "ivtm",
    "icio",
    "iae",
    "irnr",
    "aeat",
    "lgt",
)


def _strip_accents(text: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    ).lower()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", _strip_accents(text)).strip()


def _match_substrings(text: str, candidates: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(kw for kw in candidates if kw in text)


def _match_acronyms(text: str, acronyms: tuple[str, ...]) -> tuple[str, ...]:
    matches: list[str] = []
    for acr in acronyms:
        if re.search(rf"\b{re.escape(acr)}\b", text):
            matches.append(acr)
    return tuple(matches)


def classify_sentencia(parsed: ParsedSentencia) -> TaxClassification:
    """Clasifica una sentencia ya parseada como `fiscal`, `probable` o `no_fiscal`.

    Reglas:
    - Materia explícita tributaria → `fiscal`.
    - Sala 3ª Sección 2ª del TS → `fiscal` (sala fiscal canónica).
    - Sin materia explícita pero con keywords fuertes en el texto → `probable`.
    - Resto → `no_fiscal`.
    """
    materia = parsed.get_field("Materia") or ""
    sala = parsed.get_field("Sala", "Órgano") or ""
    seccion = parsed.get_field("Sección") or ""
    body = _norm(parsed.plain_text)

    norm_materia = _norm(materia)

    matched_materia = _match_substrings(norm_materia, _MATERIA_FISCAL_PATTERNS)
    if matched_materia:
        return TaxClassification(
            relevance="fiscal",
            matched_keywords=matched_materia,
            reasons=(f"materia explícita tributaria: {materia!r}",),
        )

    # Sala 3ª Sección 2ª del TS (Contencioso, Sección Segunda) es la
    # sala fiscal del Supremo. Detectamos también por "Sala Tercera" +
    # "Sección Segunda".
    norm_sala = _norm(sala)
    norm_seccion = _norm(seccion)
    if (
        ("tercera" in norm_sala or "contencioso" in norm_sala)
        and "segunda" in norm_seccion
    ):
        return TaxClassification(
            relevance="fiscal",
            matched_keywords=(),
            reasons=("Sala 3ª Sección 2ª del TS (sala fiscal canónica)",),
        )

    keywords_fuertes = _match_substrings(body, _BODY_KEYWORDS_FUERTES)
    acronimos = _match_acronyms(body, _BODY_ACRONIMOS)
    matched = keywords_fuertes + acronimos
    if matched:
        return TaxClassification(
            relevance="probable",
            matched_keywords=matched,
            reasons=(f"{len(matched)} keywords tributarias en cuerpo",),
        )

    return TaxClassification(
        relevance="no_fiscal",
        matched_keywords=(),
        reasons=("sin señales de materia tributaria",),
    )
