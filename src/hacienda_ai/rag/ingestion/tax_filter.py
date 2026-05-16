"""Clasificador de materia fiscal sobre items del sumario BOE.

El BOE publica ~300 disposiciones al día; solo una fracción son fiscalmente
relevantes para nuestro corpus. Este módulo decide, sin consultar red ni
descargar el documento completo, si un item del sumario debe pasar a la
siguiente fase del pipeline (descarga, hash, registro como `Norma`).

La heurística usa tres señales del propio sumario:

1. **Departamento emisor**: Ministerio de Hacienda (en sus distintas formas
   históricas) es señal fuerte. La Jefatura del Estado emite Leyes y
   Reales Decretos-leyes que pueden o no ser fiscales — ahí toca filtrar
   por título. Otros departamentos casi nunca emiten normativa fiscal
   (excepciones puntuales como Transición Ecológica con impuestos energéticos).

2. **Epígrafe / tipo de disposición**: "Ley", "Real Decreto-ley", "Real
   Decreto", "Orden", "Resolución" — para mapear a `SourceKind`.

3. **Palabras clave en el título**: lista positiva (impuesto, tributario,
   IRPF, IVA…) y lista negativa para reducir falsos positivos (subvenciones,
   becas, etc., aunque la palabra "fiscal" aparezca en contexto distinto).

El clasificador es deliberadamente conservador: ante la duda, **incluir**.
Un falso positivo lo descarta el revisor humano al mergear el PR. Un falso
negativo significa una norma perdida hasta que alguien la note manualmente,
lo cual es mucho más caro.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from ...models import SourceKind

TaxRelevance = Literal["fiscal", "probable", "no_fiscal"]


@dataclass(frozen=True)
class Classification:
    """Resultado del clasificador.

    `relevance`:
    - `fiscal`: señales fuertes (departamento Hacienda + palabra clave fiscal).
    - `probable`: señales débiles (Jefatura del Estado + título con keyword).
    - `no_fiscal`: descartado.

    `kind` mapea el epígrafe al enum `SourceKind`. `None` cuando el epígrafe
    no se reconoce o la disposición no es de rango normativo registrable
    (notas, anuncios, etc.).

    `reasons` deja traza humana de por qué se aceptó/rechazó: útil para el
    log del cron y para auditar el clasificador.
    """

    relevance: TaxRelevance
    kind: SourceKind | None
    matched_keywords: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def accept(self) -> bool:
        return self.relevance in ("fiscal", "probable")


# Departamentos cuya producción normativa es casi siempre fiscal. Coincide
# con las denominaciones históricas del ministerio que han aparecido en el
# BOE en las últimas décadas — el departamento ha cambiado de nombre varias
# veces (Hacienda, Hacienda y AAPP, Hacienda y Función Pública…), todas
# cuentan. La comparación es por substring tras normalizar acentos y caso.
_DEPARTAMENTOS_FISCALES = (
    "ministerio de hacienda",
    "ministerio de hacienda y funcion publica",
    "ministerio de hacienda y administraciones publicas",
    "ministerio de economia y hacienda",
    "ministerio de hacienda y economia",
    "ministerio de economia, hacienda y empresa",
)

# Departamentos que ocasionalmente emiten normativa con efectos fiscales
# (impuestos energéticos, impuestos especiales, aranceles). Si el título
# contiene una keyword fiscal y el departamento está aquí, se acepta como
# `probable`.
_DEPARTAMENTOS_OCASIONALES = (
    "jefatura del estado",  # Leyes y RDL: la mayoría de leyes fiscales.
    "ministerio para la transicion ecologica",
    "ministerio de industria",
    "ministerio de asuntos economicos",
    "ministerio de inclusion, seguridad social y migraciones",
    "presidencia del gobierno",
)

# Palabras clave fiscales que son frases largas o substrings inequívocas.
# Una coincidencia basta. Se comparan como substring tras normalizar el
# título. No incluir aquí acrónimos cortos (IVA, IRPF, IS, IP, ISD…)
# porque tienen falsos positivos por substring (`administrativa` contiene
# `iva`). Esos van en `_KEYWORDS_ACRONIMO` con word boundaries.
_KEYWORDS_FISCALES = (
    # Genéricos
    "tributari",  # tributario, tributaria, tributarias…
    "fiscal",
    "impuesto",
    "impuestos",
    "gravamen",
    "tasa fiscal",
    "tasas fiscales",
    "hacienda publica",
    "haciendas locales",
    "agencia tributaria",
    "direccion general de tributos",
    # Figuras tributarias estatales (frases)
    "renta de las personas fisicas",
    "valor añadido",
    "valor anadido",
    "sucesiones y donaciones",
    "transmisiones patrimoniales",
    "actos juridicos documentados",
    "renta de no residentes",
    "impuestos especiales",
    "primas de seguros",
    "actividades economicas",
    # Figuras locales
    "bienes inmuebles",  # IBI
    "vehiculos de traccion mecanica",  # IVTM
    "construcciones, instalaciones y obras",  # ICIO
    "incremento de valor de los terrenos",  # IIVTNU / Plusvalía municipal
    "plusvalia municipal",
    # Operativa AEAT (frases inequívocas)
    "autoliquidacion",
    "declaracion tributaria",
    "ingreso a cuenta",
    "pagos a cuenta",
    "obligaciones formales",
    "modelo 100",
    "modelo 200",
    "modelo 303",
    "modelo 347",
    "modelo 349",
    "modelo 390",
    "modelo 720",
    "modelo 721",
    "modelo 714",
    "modelo 650",
    "modelo 651",
    "modelo 232",
    "censo de obligados",
    "numero de identificacion fiscal",
    "infraccion tributaria",
    "sancion tributaria",
    "recaudacion tributaria",
    "inspeccion tributaria",
    "procedimiento tributario",
    "comprobacion limitada",
    "gestion tributaria",
    # Conceptos del IRPF/IS (frases largas, sin ambigüedad)
    "rendimientos del trabajo",
    "rendimientos de actividades economicas",
    "rendimientos del capital",
    "ganancias y perdidas patrimoniales",
    "minimo personal y familiar",
    "deduccion por maternidad",
    "deduccion por inversion",
    # Cripto
    "monedas virtuales",
    "criptoactivos",
    "criptomonedas",
)

# Acrónimos y palabras cortas que solo deben matchear como palabras completas
# (con word boundaries) para evitar falsos positivos por substring. Ejemplo:
# `administrativa` contiene `iva` pero no se refiere al impuesto.
_KEYWORDS_ACRONIMO = (
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
    "lirpf",
    "lis",
    "liva",
    "lisd",
    "lip",
    "retencion",
    "retenciones",
    "sociedades",  # como "Impuesto sobre Sociedades" — no como adjetivo "sociales"
    "patrimonio",  # como impuesto
)

# Palabras clave que vetan la inclusión incluso si hay una keyword fiscal.
# Sirven para descartar normas donde "fiscal" aparece como adjetivo en
# contextos no tributarios (ej. "año fiscal" en presupuestos, "fiscal" como
# adjetivo de Ministerio Fiscal, etc.). Lista muy corta y conservadora.
_NEGATIVE_KEYWORDS = (
    "ministerio fiscal",
    "carrera fiscal",
    "fiscal general del estado",
    "fiscalia",  # cualquier referencia a fiscalías de la jurisdicción.
)

# Mapeo de epígrafes del sumario BOE a `SourceKind`. El sumario expone el
# tipo de disposición en `epigrafe.nombre`; los valores son estables a
# nivel de string ("Ley", "Real Decreto-ley", etc.).
#
# El orden importa: comprobamos los prefijos más largos primero para evitar
# que "Real Decreto" capture "Real Decreto-ley" o "Real Decreto Legislativo".
_EPIGRAFE_TO_KIND: tuple[tuple[str, SourceKind], ...] = (
    ("ley organica", SourceKind.LEY_ORGANICA),
    ("real decreto legislativo", SourceKind.REAL_DECRETO_LEGISLATIVO),
    ("real decreto-ley", SourceKind.REAL_DECRETO_LEY),
    ("real decreto ley", SourceKind.REAL_DECRETO_LEY),
    ("real decreto", SourceKind.REAL_DECRETO),
    ("orden", SourceKind.ORDEN_MINISTERIAL),
    ("resolucion", SourceKind.RESOLUCION),
    ("ley", SourceKind.LEY),
)


def _strip_accents(text: str) -> str:
    """Quita tildes y normaliza a minúsculas, conservando ñ→n para matching."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower()


def _normalize(text: str) -> str:
    """Normaliza para comparación: minúsculas, sin tildes, espacios colapsados."""
    return re.sub(r"\s+", " ", _strip_accents(text)).strip()


def map_epigrafe_to_kind(epigrafe: str) -> SourceKind | None:
    """Mapea el `nombre` del epígrafe del sumario a `SourceKind`.

    Devuelve `None` si el epígrafe no es de rango normativo registrable
    (ej. "Anuncio", "Notificación", "Nombramientos"). Esos items no entran
    al corpus de normas — el corpus solo guarda disposiciones generales y
    actos con efecto normativo.
    """
    normalized = _normalize(epigrafe)
    for prefix, kind in _EPIGRAFE_TO_KIND:
        if normalized.startswith(prefix):
            return kind
    return None


def _match_keywords(text: str, keywords: tuple[str, ...]) -> tuple[str, ...]:
    matches = [kw for kw in keywords if kw in text]
    return tuple(matches)


def _match_acronyms(text: str, acronyms: tuple[str, ...]) -> tuple[str, ...]:
    """Match con word boundaries: `iva` matchea `del IVA` pero no `administrativa`."""
    matches: list[str] = []
    for acr in acronyms:
        pattern = re.compile(rf"\b{re.escape(acr)}\b")
        if pattern.search(text):
            matches.append(acr)
    return tuple(matches)


def classify(
    departamento: str,
    epigrafe: str,
    titulo: str,
) -> Classification:
    """Clasifica un item del sumario BOE como fiscalmente relevante o no.

    Reglas (en orden de evaluación):
    1. Si el epígrafe no mapea a un `SourceKind` registrable → `no_fiscal`.
    2. Si el título dispara una negative keyword (ej. "Ministerio Fiscal") → `no_fiscal`.
    3. Si el departamento es Hacienda → `fiscal` (kind del epígrafe).
    4. Si el departamento está en `_DEPARTAMENTOS_OCASIONALES` Y el título
       contiene una keyword fiscal → `probable`.
    5. Resto → `no_fiscal`.
    """
    norm_dep = _normalize(departamento)
    norm_titulo = _normalize(titulo)
    kind = map_epigrafe_to_kind(epigrafe)

    if kind is None:
        return Classification(
            relevance="no_fiscal",
            kind=None,
            matched_keywords=(),
            reasons=(f"epigrafe no normativo: {epigrafe!r}",),
        )

    negative_hits = _match_keywords(norm_titulo, _NEGATIVE_KEYWORDS)
    if negative_hits:
        return Classification(
            relevance="no_fiscal",
            kind=kind,
            matched_keywords=(),
            reasons=(f"negative keyword: {', '.join(negative_hits)}",),
        )

    is_hacienda = any(dep in norm_dep for dep in _DEPARTAMENTOS_FISCALES)
    is_ocasional = any(dep in norm_dep for dep in _DEPARTAMENTOS_OCASIONALES)
    matched = _match_keywords(norm_titulo, _KEYWORDS_FISCALES) + _match_acronyms(
        norm_titulo, _KEYWORDS_ACRONIMO
    )

    if is_hacienda:
        return Classification(
            relevance="fiscal",
            kind=kind,
            matched_keywords=matched,
            reasons=(f"departamento fiscal: {departamento!r}",),
        )

    if is_ocasional and matched:
        return Classification(
            relevance="probable",
            kind=kind,
            matched_keywords=matched,
            reasons=(
                f"departamento ocasional: {departamento!r}",
                f"keywords: {', '.join(matched)}",
            ),
        )

    return Classification(
        relevance="no_fiscal",
        kind=kind,
        matched_keywords=matched,
        reasons=(
            f"departamento no fiscal: {departamento!r}",
            f"keywords match: {len(matched)}",
        ),
    )
