"""Catálogo de operaciones típicas IVA con su tipo y cita pinpoint.

El catálogo es **seed**: cubre operaciones frecuentes que aparecen en
preguntas de usuario tipo "¿qué IVA llevan los libros?" o "¿qué tipo se
aplica a un servicio profesional?". Cada entrada incluye:

- `keyword`: forma canónica corta usada para matchear consultas (sin
  acentos, minúsculas).
- `description`: descripción humana exhaustiva con matices habituales.
- `tipo`: el `IVATipo` aplicable según la LIVA.
- `source`: `Source` con BOE-ID + artículo (apartado solo cuando la
  asignación tipo↔operación está claramente regulada al nivel del
  apartado). Para reducir el riesgo de citar un ordinal incorrecto
  cuando la norma se ha reformado, **citamos el apartado de
  cabecera** (`art. 91.1` para reducidos, `art. 91.2` para
  superreducidos) salvo evidencia clara de un ordinal estable.
- `notes`: matices o avisos (regla con vigencia limitada, casos
  particulares).

La búsqueda es léxica con normalización ASCII y matching por todos los
tokens de la query. NO usa embeddings — para algo así de pequeño y
estructurado, BM25 ya sería sobreingeniería; la búsqueda exacta por
keyword da resultados verificables.

EXCLUSIONES (deliberadas, no inventamos):
- Operaciones con tipo afectado por medidas coyunturales recientes
  (alimentos 0% del RD-Ley 20/2022 hasta jun-2024; electricidad
  reducida temporal): el LLM debe pedir devengo exacto antes de
  responder.
- Operaciones donde el tipo depende del destinatario (sociedad vs.
  particular, B2B vs. B2C) o del lugar (intracomunitaria, exportación):
  esos casos exigen un razonamiento previo que esta tool no hace.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from ..models import Source
from ..models.norma import SourceKind
from .tipos import LIVA_BOE_ID, IVATipo


@dataclass(frozen=True)
class IVAOperation:
    """Operación típica con su tipo IVA y cita pinpoint."""

    keyword: str
    description: str
    tipo: IVATipo
    source: Source
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "description": self.description,
            "tipo": self.tipo.value,
            "source": {
                "boe_id": self.source.boe_id,
                "article": self.source.article,
                "paragraph": self.source.paragraph,
                "title": self.source.title,
            },
            "notes": self.notes,
        }


def _src(article: str, paragraph: str | None, title: str) -> Source:
    return Source(
        kind=SourceKind.LEY,
        title=title,
        boe_id=LIVA_BOE_ID,
        article=article,
        paragraph=paragraph,
    )


# Citas de cabecera reutilizables.
_SRC_91_1 = _src("art. 91", "1", "LIVA art. 91.1 — tipo reducido 10%")
_SRC_91_2 = _src("art. 91", "2", "LIVA art. 91.2 — tipo superreducido 4%")
_SRC_90 = _src("art. 90", None, "LIVA art. 90 — tipo general 21%")
_SRC_20 = _src("art. 20", None, "LIVA art. 20 — exenciones operaciones interiores")
_SRC_21 = _src("art. 21", None, "LIVA art. 21 — exoneración en exportaciones")


CATALOG: tuple[IVAOperation, ...] = (
    # ----- Superreducido (4%) -----
    IVAOperation(
        keyword="pan comun",
        description=(
            "Pan común (no especiales tipo pan integral con frutos secos, "
            "que tributan al reducido)."
        ),
        tipo=IVATipo.SUPERREDUCIDO,
        source=_SRC_91_2,
    ),
    IVAOperation(
        keyword="leche",
        description="Leche natural, certificada, pasteurizada, concentrada, evaporada, en polvo.",
        tipo=IVATipo.SUPERREDUCIDO,
        source=_SRC_91_2,
    ),
    IVAOperation(
        keyword="huevos",
        description="Huevos de ave.",
        tipo=IVATipo.SUPERREDUCIDO,
        source=_SRC_91_2,
    ),
    IVAOperation(
        keyword="frutas hortalizas",
        description="Frutas, verduras, hortalizas, legumbres, tubérculos y cereales naturales.",
        tipo=IVATipo.SUPERREDUCIDO,
        source=_SRC_91_2,
    ),
    IVAOperation(
        keyword="libros impresos",
        description=(
            "Libros, periódicos y revistas impresos que no contengan única o "
            "fundamentalmente publicidad. Extendido también a la versión "
            "digital de los mismos."
        ),
        tipo=IVATipo.SUPERREDUCIDO,
        source=_SRC_91_2,
    ),
    IVAOperation(
        keyword="medicamentos uso humano",
        description=(
            "Medicamentos para uso humano, formas galénicas, sustancias "
            "medicinales y productos intermedios para su elaboración."
        ),
        tipo=IVATipo.SUPERREDUCIDO,
        source=_SRC_91_2,
    ),
    IVAOperation(
        keyword="silla ruedas",
        description=(
            "Vehículos para personas con movilidad reducida (sillas de "
            "ruedas) y prótesis, órtesis e implantes internos para personas "
            "con discapacidad."
        ),
        tipo=IVATipo.SUPERREDUCIDO,
        source=_SRC_91_2,
    ),
    IVAOperation(
        keyword="vivienda proteccion oficial",
        description=(
            "Entrega de viviendas calificadas administrativamente como de "
            "protección oficial de régimen especial o de promoción pública, "
            "incluyendo plazas de garaje (máximo dos) y anejos transmitidos "
            "conjuntamente."
        ),
        tipo=IVATipo.SUPERREDUCIDO,
        source=_SRC_91_2,
    ),
    # ----- Reducido (10%) -----
    IVAOperation(
        keyword="alimentos en general",
        description=(
            "Alimentos y bebidas no incluidos en el superreducido (p. ej. "
            "carnes, pescados, aceites no de oliva ni semillas, preparados "
            "alimenticios)."
        ),
        tipo=IVATipo.REDUCIDO,
        source=_SRC_91_1,
    ),
    IVAOperation(
        keyword="agua",
        description="Aguas aptas para alimentación humana o animal y para el riego.",
        tipo=IVATipo.REDUCIDO,
        source=_SRC_91_1,
    ),
    IVAOperation(
        keyword="hosteleria restauracion",
        description=(
            "Servicios de hostelería, restauración, acampamento y suministros "
            "de comidas y bebidas para consumir en el acto."
        ),
        tipo=IVATipo.REDUCIDO,
        source=_SRC_91_1,
    ),
    IVAOperation(
        keyword="transporte viajeros",
        description=(
            "Transporte de viajeros y sus equipajes (autobús urbano, "
            "interurbano, taxi, tren, metro, avión doméstico)."
        ),
        tipo=IVATipo.REDUCIDO,
        source=_SRC_91_1,
    ),
    IVAOperation(
        keyword="entradas espectaculos",
        description=(
            "Entradas a bibliotecas, archivos, museos, teatros, conciertos, "
            "cines, exposiciones y eventos culturales similares."
        ),
        tipo=IVATipo.REDUCIDO,
        source=_SRC_91_1,
    ),
    IVAOperation(
        keyword="vivienda nueva",
        description=(
            "Entrega de viviendas (no protegidas) incluyendo plazas de "
            "garaje (máximo dos) y anejos transmitidos conjuntamente. NO "
            "aplica a locales comerciales."
        ),
        tipo=IVATipo.REDUCIDO,
        source=_SRC_91_1,
    ),
    IVAOperation(
        keyword="reformas vivienda",
        description=(
            "Ejecuciones de obra de renovación y reparación realizadas en "
            "viviendas particulares cuando el destinatario es una persona "
            "física (no actividad económica) y se cumplen requisitos "
            "específicos (antigüedad, materiales aportados por el "
            "ejecutor < 40% del total)."
        ),
        tipo=IVATipo.REDUCIDO,
        source=_SRC_91_1,
        notes=(
            "Aplicación condicionada a requisitos del art. 91.1 LIVA. "
            "Verificar caso a caso."
        ),
    ),
    IVAOperation(
        keyword="gafas graduadas lentillas",
        description="Gafas y lentes graduadas, productos sanitarios listados.",
        tipo=IVATipo.REDUCIDO,
        source=_SRC_91_1,
    ),
    IVAOperation(
        keyword="flores plantas",
        description="Flores y plantas vivas de carácter ornamental.",
        tipo=IVATipo.REDUCIDO,
        source=_SRC_91_1,
    ),
    IVAOperation(
        keyword="servicios funerarios",
        description="Servicios funerarios efectuados por las empresas funerarias.",
        tipo=IVATipo.REDUCIDO,
        source=_SRC_91_1,
    ),
    # ----- General (21%) -----
    IVAOperation(
        keyword="servicios profesionales",
        description=(
            "Servicios profesionales prestados por abogados, asesores, "
            "consultores, contables, arquitectos, ingenieros, etc. cuando "
            "no quepa otro tipo específico."
        ),
        tipo=IVATipo.GENERAL,
        source=_SRC_90,
    ),
    IVAOperation(
        keyword="bebidas alcoholicas",
        description="Bebidas alcohólicas (cerveza, vino, licores, destilados).",
        tipo=IVATipo.GENERAL,
        source=_SRC_90,
    ),
    IVAOperation(
        keyword="combustibles",
        description="Gasolinas, gasóleos, GLP y otros carburantes para automoción.",
        tipo=IVATipo.GENERAL,
        source=_SRC_90,
    ),
    IVAOperation(
        keyword="electrodomesticos",
        description="Electrodomésticos, equipos electrónicos de consumo.",
        tipo=IVATipo.GENERAL,
        source=_SRC_90,
    ),
    IVAOperation(
        keyword="ropa calzado",
        description="Ropa, calzado y complementos textiles.",
        tipo=IVATipo.GENERAL,
        source=_SRC_90,
    ),
    IVAOperation(
        keyword="local comercial",
        description="Entrega y arrendamiento (a sociedades o profesionales) de locales comerciales.",
        tipo=IVATipo.GENERAL,
        source=_SRC_90,
    ),
    # ----- Exento -----
    IVAOperation(
        keyword="servicios medicos",
        description=(
            "Servicios sanitarios prestados por profesionales médicos o "
            "sanitarios (incluidos los servicios de hospitalización)."
        ),
        tipo=IVATipo.EXENTO,
        source=_SRC_20,
        notes="Exención del art. 20.uno.3º LIVA. No genera derecho a deducir IVA soportado.",
    ),
    IVAOperation(
        keyword="ensenanza reglada",
        description=(
            "Enseñanza en centros docentes públicos o privados autorizados, "
            "y clases particulares por personas físicas sobre materias del "
            "plan de estudios."
        ),
        tipo=IVATipo.EXENTO,
        source=_SRC_20,
        notes="Exención del art. 20.uno.9º y 10º LIVA.",
    ),
    IVAOperation(
        keyword="alquiler vivienda",
        description=(
            "Arrendamientos de edificaciones destinadas exclusivamente a "
            "vivienda (no apartamentos turísticos con servicios hoteleros)."
        ),
        tipo=IVATipo.EXENTO,
        source=_SRC_20,
        notes="Exención del art. 20.uno.23º LIVA.",
    ),
    IVAOperation(
        keyword="operaciones financieras",
        description=(
            "Operaciones de crédito, fianzas, garantías, operaciones de pago "
            "y depósitos, así como la negociación de divisas y valores."
        ),
        tipo=IVATipo.EXENTO,
        source=_SRC_20,
        notes="Exención del art. 20.uno.18º LIVA.",
    ),
    IVAOperation(
        keyword="seguros",
        description="Operaciones de seguro, reaseguro y capitalización.",
        tipo=IVATipo.EXENTO,
        source=_SRC_20,
        notes="Exención del art. 20.uno.16º LIVA.",
    ),
    # ----- Cero (operaciones exoneradas con derecho a deducción) -----
    IVAOperation(
        keyword="exportacion bienes",
        description=(
            "Entregas de bienes expedidos o transportados fuera de la UE por "
            "el transmitente o por el adquirente no establecido (exportación)."
        ),
        tipo=IVATipo.CERO,
        source=_SRC_21,
        notes=(
            "Operación gravada al 0% (no exenta): da derecho a deducir el "
            "IVA soportado en los inputs."
        ),
    ),
)


# ---------- Búsqueda ----------


def _normalize(text: str) -> str:
    """Minúsculas + sin acentos + colapso de espacios."""
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(ascii_only.lower().split())


def lookup_iva_operations(query: str) -> list[IVAOperation]:
    """Busca operaciones cuyo keyword/description contienen TODOS los
    tokens de la query (matching léxico, normalizado a ASCII sin
    acentos, minúsculas).

    Devuelve la lista de matches en el orden del catálogo (la entrada
    canónica más obvia primero). Una query vacía devuelve [].
    """
    tokens = _normalize(query).split()
    if not tokens:
        return []
    matches: list[IVAOperation] = []
    for op in CATALOG:
        haystack = _normalize(f"{op.keyword} {op.description}")
        if all(token in haystack for token in tokens):
            matches.append(op)
    return matches


def iva_documented_sources() -> list[Source]:
    """Devuelve TODAS las `Source` citadas por el módulo IVA.

    Pensado para concatenar con el corpus de deducciones a la hora de
    construir el índice del `citation_guard`: sin esto, las citas IVA
    del LLM serían marcadas como `ARTICLE_NOT_IN_CORPUS` aunque sean
    correctas. Se desduplica por (boe_id, article, paragraph).
    """
    from .tipos import IVA_SOURCES

    seen: set[tuple[str | None, str | None, str | None]] = set()
    out: list[Source] = []
    for source in (*IVA_SOURCES.values(), *(op.source for op in CATALOG)):
        key = (source.boe_id, source.article, source.paragraph)
        if key in seen:
            continue
        seen.add(key)
        out.append(source)
    return out


__all__ = [
    "CATALOG",
    "IVAOperation",
    "iva_documented_sources",
    "lookup_iva_operations",
]
