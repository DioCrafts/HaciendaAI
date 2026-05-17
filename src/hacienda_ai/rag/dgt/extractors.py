"""Extractores heurísticos para consultas DGT.

Tres piezas:

1. **`detect_impuesto`** — clasifica la consulta por tributo principal
   (IRPF, IVA, IS, IP, ISD, IRNR, ITP-AJD, IIVTNU, IBI, IAE, LGT, OTRO).
   Señales: cabecera `Normativa` + asunto/materia + cuerpo. Una consulta
   puede tocar varios; devolvemos el principal (el primero detectado
   por orden de evaluación).

2. **`extract_normativa`** — lista de citas normativas concretas del
   cuerpo (`Ley 35/2006 art. 19.2.e)`, `RIRPF art. 10`,
   `art. 7 LIS`…). Útil para invalidar el corpus cuando una norma
   cambie.

3. **`extract_criterio`** — extracto del criterio doctrinal del cierre
   de la contestación. Estrategia paralela a `ratio_decidendi` de
   jurisprudencia:
   - Buscar frases-marcador ("en consecuencia", "por tanto", "esta
     Dirección General considera", "se concluye que").
   - Fallback: último párrafo no trivial de la contestación.

   Marcado con `CriterioConfidence.AUTO`. Un revisor humano lo
   promociona a `MANUAL` cuando lo valida.
"""

from __future__ import annotations

import re
import unicodedata

from ...models import Impuesto

# ---------- detect_impuesto ----------

# Patrones por impuesto: clave del enum + lista de marcadores que
# indican esa figura. El orden importa: se evalúa de más específico a
# más genérico (IRNR antes que IRPF para distinguir no residentes;
# IIVTNU antes que cualquier mención de "valor" genérico).
_IMPUESTO_PATTERNS: tuple[tuple[Impuesto, tuple[str, ...]], ...] = (
    (
        Impuesto.IRNR,
        (
            "impuesto sobre la renta de no residentes",
            "irnr",
            "ley 5/2004",
            "no residentes",
        ),
    ),
    (
        Impuesto.IRPF,
        (
            "impuesto sobre la renta de las personas fisicas",
            "irpf",
            "ley 35/2006",
        ),
    ),
    (
        Impuesto.IS,
        (
            "impuesto sobre sociedades",
            "ley 27/2014",
        ),
    ),
    (
        Impuesto.IVA,
        (
            "impuesto sobre el valor anadido",
            "ley 37/1992",
        ),
    ),
    (
        Impuesto.IP,
        (
            "impuesto sobre el patrimonio",
            "ley 19/1991",
        ),
    ),
    (
        Impuesto.ISD,
        (
            "impuesto sobre sucesiones y donaciones",
            "ley 29/1987",
        ),
    ),
    (
        Impuesto.ITP_AJD,
        (
            "transmisiones patrimoniales y actos juridicos documentados",
            "transmisiones patrimoniales",
            "actos juridicos documentados",
            "rdleg 1/1993",
            "real decreto legislativo 1/1993",
        ),
    ),
    (
        Impuesto.IIVTNU,
        (
            "incremento de valor de los terrenos",
            "iivtnu",
            "plusvalia municipal",
        ),
    ),
    (
        Impuesto.IBI,
        ("impuesto sobre bienes inmuebles", "ibi"),
    ),
    (
        Impuesto.IAE,
        ("impuesto sobre actividades economicas", "iae"),
    ),
    (
        Impuesto.LGT,
        (
            "ley general tributaria",
            "ley 58/2003",
            "procedimiento tributario",
        ),
    ),
)

# Acrónimos que SOLO se aceptan con word-boundary. Sin esto,
# `administrativa` matchearía `iva`.
_IMPUESTO_ACRONIMOS: dict[Impuesto, tuple[str, ...]] = {
    Impuesto.IVA: ("iva",),
    Impuesto.IS: ("is",),  # cuidado: muy genérico; lo gestionamos con WB.
    Impuesto.IP: ("ip",),
    Impuesto.ISD: ("isd",),
    Impuesto.IRPF: ("irpf",),
    Impuesto.IRNR: ("irnr",),
    Impuesto.IIVTNU: ("iibtnu", "iivtnu"),
    Impuesto.IBI: ("ibi",),
    Impuesto.IAE: ("iae",),
    Impuesto.LGT: ("lgt",),
    Impuesto.ITP_AJD: ("itp", "ajd"),
}


def _strip_accents(text: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    ).lower()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", _strip_accents(text)).strip()


def detect_impuesto(
    *,
    normativa: str | None,
    asunto: str | None,
    cuerpo: str,
) -> Impuesto:
    """Devuelve el impuesto principal de la consulta.

    Política: priorizamos señales en `normativa` y `asunto` (cabecera,
    explícitas) sobre el cuerpo (más ruidoso). Si ninguna señal casa,
    devolvemos `Impuesto.OTRO`.
    """
    # Concatenamos cabecera con peso alto: una mención en `normativa` o
    # `asunto` debe ganar a la primera mención del cuerpo, incluso si el
    # cuerpo tiene otra figura que aparece después.
    cabecera = " ".join(filter(None, [normativa, asunto]))
    norm_cabecera = _norm(cabecera)
    norm_cuerpo = _norm(cuerpo)

    # 1. Frases largas en cabecera (señal más fuerte).
    for impuesto, patterns in _IMPUESTO_PATTERNS:
        if any(p in norm_cabecera for p in patterns):
            return impuesto

    # 2. Frases largas en cuerpo.
    for impuesto, patterns in _IMPUESTO_PATTERNS:
        if any(p in norm_cuerpo for p in patterns):
            return impuesto

    # 3. Acrónimos con word boundary (cabecera).
    for impuesto, acronimos in _IMPUESTO_ACRONIMOS.items():
        for acr in acronimos:
            if re.search(rf"\b{re.escape(acr)}\b", norm_cabecera):
                return impuesto

    # 4. Acrónimos con word boundary (cuerpo).
    for impuesto, acronimos in _IMPUESTO_ACRONIMOS.items():
        for acr in acronimos:
            if re.search(rf"\b{re.escape(acr)}\b", norm_cuerpo):
                return impuesto

    return Impuesto.OTRO


# ---------- extract_normativa ----------

# Capturamos referencias normativas frecuentes en consultas DGT.
# Patrones (orden no importa, deduplicamos al final):
#   "Ley 35/2006 art. 19.2.e)"
#   "Real Decreto 439/2007, art. 10"
#   "art. 7 LIS"
#   "artículo 96 LIRPF"
_RE_NORMATIVA_LEY = re.compile(
    r"\b(?:Ley|LO|Ley\s+Org[áa]nica|Real\s+Decreto(?:\s+Legislativo|\s+Ley)?|Orden)\s+"
    r"\d+/\d{2,4}"
    r"(?:[^.\n]*?\bart(?:[íi]culo|\.)?\s*[\d.]+(?:\s*\.?\s*[a-z]\))?)?",
    re.IGNORECASE,
)

_RE_NORMATIVA_ALIAS = re.compile(
    r"\bart(?:[íi]culo|\.)?\s*[\d.]+(?:\s*\.?\s*[a-z]\))?\s+"
    r"(?:LIRPF|LIS|LIVA|LGT|LISD|LIP|LIRNR|TRLITP|RIRPF|RIS|RIVA)\b",
    re.IGNORECASE,
)


def extract_normativa(plain_text: str, normativa_header: str | None) -> tuple[str, ...]:
    """Devuelve lista deduplicada y ordenada de citas normativas detectadas.

    Combina el campo `Normativa` de la cabecera (si existe, gold) con
    las menciones detectadas en el cuerpo.
    """
    citas: list[str] = []
    if normativa_header:
        # El campo `Normativa` de Petete suele venir como lista separada
        # por comas o saltos de línea. Lo partimos por comas y limpiamos.
        for chunk in re.split(r"[\n,]+", normativa_header):
            chunk = chunk.strip()
            if chunk and len(chunk) > 5:
                citas.append(chunk)

    for match in _RE_NORMATIVA_LEY.finditer(plain_text):
        cita = re.sub(r"\s+", " ", match.group(0)).strip()
        if len(cita) <= 200:  # filtramos matches absurdamente largos.
            citas.append(cita)

    for match in _RE_NORMATIVA_ALIAS.finditer(plain_text):
        cita = re.sub(r"\s+", " ", match.group(0)).strip()
        citas.append(cita)

    # Deduplicamos preservando orden de aparición (gold primero).
    seen: set[str] = set()
    out: list[str] = []
    for cita in citas:
        key = cita.lower()
        if key not in seen:
            seen.add(key)
            out.append(cita)
    return tuple(out)


# ---------- extract_criterio ----------

# Marcadores de cierre doctrinal típicos en contestaciones DGT.
_MARCADORES_CRITERIO = (
    re.compile(
        r"\besta\s+Direcci[óo]n\s+General\s+(?:considera|entiende|interpreta|concluye)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bse\s+concluye\s+que\b", re.IGNORECASE),
    re.compile(r"\ben\s+consecuencia,?\b", re.IGNORECASE),
    re.compile(r"\bpor\s+(?:lo\s+)?tanto,?\b", re.IGNORECASE),
    re.compile(r"\bdebe\s+concluirse\s+que\b", re.IGNORECASE),
    re.compile(r"\bcabe\s+concluir\s+que\b", re.IGNORECASE),
    # No incluimos "la presente contestación se emite con carácter
    # vinculante": es la coletilla burocrática final, no doctrina —
    # ganaría al criterio real por ser el último marcador del texto.
)


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]


def _max_chars(text: str, limit: int = 1500) -> str:
    """Trunca a `limit` chars sin partir palabras (deja " […]")."""
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    cut = truncated.rfind(" ")
    return (truncated[:cut] if cut > 0 else truncated) + " […]"


def extract_criterio(
    plain_text: str,
    *,
    contestacion_section: str | None = None,
) -> str | None:
    """Extrae el criterio doctrinal de la contestación.

    Estrategia:
    1. Buscar marcadores de cierre ("en consecuencia", "esta DG
       considera", "se concluye que"). Devolver el párrafo donde
       aparecen — suele ser la conclusión decisiva.
    2. Fallback: último párrafo no trivial de la contestación.
    3. Si nada, devolver `None`.

    Truncado a 1500 chars. El campo se almacena SIEMPRE con
    `CriterioConfidence.AUTO`; un humano debe validarlo.
    """
    source = contestacion_section or plain_text
    if not source or not source.strip():
        return None

    paragraphs = _split_paragraphs(source)
    if not paragraphs:
        return None

    # Recorrer marcadores en orden. Si encontramos varios, preferimos el
    # del último párrafo (más cerca del cierre, suele ser el verdadero
    # criterio).
    matches_por_marcador: list[tuple[int, str]] = []
    for marcador in _MARCADORES_CRITERIO:
        for idx, para in enumerate(paragraphs):
            if marcador.search(para):
                matches_por_marcador.append((idx, para))
    if matches_por_marcador:
        # El de mayor índice (más al final).
        matches_por_marcador.sort(key=lambda x: x[0])
        return _max_chars(matches_por_marcador[-1][1])

    # Fallback: último párrafo no trivial.
    last = paragraphs[-1] if len(paragraphs[-1]) > 50 else None
    if last is None and len(paragraphs) >= 2:
        last = paragraphs[-2] if len(paragraphs[-2]) > 50 else None
    return _max_chars(last) if last else None
