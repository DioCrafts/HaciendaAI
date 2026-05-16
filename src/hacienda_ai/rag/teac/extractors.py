"""Extractores heurísticos para resoluciones TEAC/TEAR.

Cinco piezas:

1. **`detect_tipo`** — distingue `UNIFICA_CRITERIO` (art. 242 LGT),
   `EXTIENDE_EFECTOS` (art. 244 LGT) y `ORDINARIA`. Crítico para el
   peso doctrinal:
   - Unificación de criterio: vincula a AEAT y a todos los TEAR.
   - Extensión de efectos: vincula a AEAT en supuestos análogos.
   - Ordinaria: criterio del caso, no vinculante para futuros.

2. **`detect_sentido`** — estimatoria/parcial/desestimatoria/inadmisión/
   retroacción/archivo/desconocido. Mismo enfoque que CENDOJ pero con
   verbos específicos del TEA ("ESTIMAR la reclamación", "DECLARAR la
   improcedencia", "ORDENAR la retroacción"…).

3. **`detect_impuesto`** — reutiliza el detector de DGT (mismo enum).
   Las señales son las mismas (Normativa, Asunto, Concepto, cuerpo).

4. **`extract_normativa`** — citas normativas detectadas. Combina campo
   `Normativa` de cabecera con menciones en cuerpo.

5. **`extract_criterio`** — extracto del criterio doctrinal:
   - Si el HTML tiene sección `CRITERIO` explícita, la devuelve verbatim
     (es lo más limpio: el TEAC ya ha sintetizado).
   - Si no, busca marcadores ("este Tribunal Central considera",
     "fija el criterio", "establece como doctrina") y devuelve el
     párrafo que los contiene.
   - Fallback: último párrafo no trivial.

   Marcado siempre `CriterioConfidence.AUTO`.
"""

from __future__ import annotations

import re

from ...models import Impuesto, SentidoResolucion, TipoResolucion
from ..dgt.extractors import detect_impuesto as _detect_impuesto_dgt
from ..dgt.extractors import extract_normativa as _extract_normativa_dgt

# ---------- detect_tipo ----------

# Indicadores muy específicos en cabecera o cuerpo de la resolución.
# Patrones evaluados en orden: el primero que matchea gana, por eso
# `UNIFICA_CRITERIO` se prueba antes que `EXTIENDE_EFECTOS`.
_TIPO_PATTERNS: tuple[tuple[TipoResolucion, tuple[re.Pattern[str], ...]], ...] = (
    (
        TipoResolucion.UNIFICA_CRITERIO,
        (
            re.compile(r"\bunificaci[óo]n\s+de\s+criterio\b", re.IGNORECASE),
            re.compile(r"\brecurso\s+extraordinario\s+(?:de\s+)?unificaci[óo]n", re.IGNORECASE),
            re.compile(r"\bart[íi]culo\s+242\b", re.IGNORECASE),
        ),
    ),
    (
        TipoResolucion.EXTIENDE_EFECTOS,
        (
            re.compile(r"\bextensi[óo]n\s+de\s+efectos\b", re.IGNORECASE),
            re.compile(r"\bart[íi]culo\s+244\b", re.IGNORECASE),
        ),
    ),
)


def detect_tipo(
    *,
    tipo_header: str | None,
    asunto: str | None,
    cuerpo: str,
) -> TipoResolucion:
    """Devuelve el tipo de resolución (vinculación doctrinal).

    Política: priorizamos el campo `Tipo de Resolución` de la cabecera
    cuando existe (señal explícita del DYCTEA), después el asunto, y
    finalmente el cuerpo.
    """
    cabecera = " ".join(filter(None, [tipo_header, asunto]))
    for tipo, patterns in _TIPO_PATTERNS:
        if cabecera and any(p.search(cabecera) for p in patterns):
            return tipo
    for tipo, patterns in _TIPO_PATTERNS:
        if any(p.search(cuerpo) for p in patterns):
            return tipo
    return TipoResolucion.ORDINARIA


# ---------- detect_sentido ----------

# Verbos canónicos del TEA, en orden de evaluación (parciales antes que
# totales para no confundir).
_SENTIDO_PATTERNS: tuple[tuple[re.Pattern[str], SentidoResolucion], ...] = (
    (
        re.compile(r"\bestimar(?:\s+parcialmente|\s+en\s+parte)\b", re.IGNORECASE),
        SentidoResolucion.ESTIMATORIA_PARCIAL,
    ),
    (
        re.compile(r"\bestimaci[óo]n\s+parcial\b", re.IGNORECASE),
        SentidoResolucion.ESTIMATORIA_PARCIAL,
    ),
    (
        re.compile(r"\bdesestimar\b", re.IGNORECASE),
        SentidoResolucion.DESESTIMATORIA,
    ),
    (
        re.compile(r"\bdesestimaci[óo]n\b", re.IGNORECASE),
        SentidoResolucion.DESESTIMATORIA,
    ),
    (
        re.compile(r"\binadmitir\b|\binadmisi[óo]n\b", re.IGNORECASE),
        SentidoResolucion.INADMISION,
    ),
    (
        re.compile(r"\bordenar\s+la\s+retroacci[óo]n\b", re.IGNORECASE),
        SentidoResolucion.RETROACCION,
    ),
    (
        re.compile(r"\bretroacci[óo]n\s+de\s+actuaciones\b", re.IGNORECASE),
        SentidoResolucion.RETROACCION,
    ),
    (
        re.compile(r"\barchivar\b|\barchivo\s+del\s+expediente\b", re.IGNORECASE),
        SentidoResolucion.ARCHIVO,
    ),
    (
        re.compile(r"\bestimar\b", re.IGNORECASE),
        SentidoResolucion.ESTIMATORIA,
    ),
    (
        re.compile(r"\bestimaci[óo]n\b", re.IGNORECASE),
        SentidoResolucion.ESTIMATORIA,
    ),
)


def detect_sentido(
    plain_text: str, fallo_section: str | None
) -> SentidoResolucion:
    """Devuelve el sentido de la resolución, normalizado.

    Igual estrategia que `extract_fallo` de jurisprudencia: priorizamos
    la sección de fallo si está disponible (más limpio), si no caemos al
    final del texto.
    """
    source = fallo_section or plain_text
    if not source.strip():
        return SentidoResolucion.DESCONOCIDO
    # Limitamos a los últimos 2000 chars: el sentido está al cierre. Si
    # la sección de fallo es más corta, usamos toda; si es larga (todo
    # el texto), nos quedamos con el final.
    snippet = source.strip()[-2000:] if len(source) > 2000 else source.strip()
    for pattern, sentido in _SENTIDO_PATTERNS:
        if pattern.search(snippet):
            return sentido
    return SentidoResolucion.DESCONOCIDO


# ---------- detect_impuesto (reuse DGT) ----------


def detect_impuesto(
    *,
    normativa: str | None,
    materia: str | None,
    cuerpo: str,
) -> Impuesto:
    """Detector de impuesto principal. Reutiliza el detector DGT.

    La señal cambia ligeramente: en TEAC el campo más informativo de
    cabecera es `Materia` o `Concepto`, no `Asunto`. Lo pasamos como
    `asunto` al detector compartido.
    """
    return _detect_impuesto_dgt(
        normativa=normativa,
        asunto=materia,
        cuerpo=cuerpo,
    )


# ---------- extract_normativa (reuse DGT) ----------


def extract_normativa(
    plain_text: str, normativa_header: str | None
) -> tuple[str, ...]:
    """Lista deduplicada de citas normativas. Reutiliza el extractor DGT.

    Las regex (`Ley X/YYYY`, `art. X LIRPF`, etc.) son comunes a
    consultas DGT y resoluciones TEAC: ambas instituciones citan la
    misma normativa con la misma sintaxis.
    """
    return _extract_normativa_dgt(plain_text, normativa_header)


# ---------- extract_criterio ----------

# Marcadores de criterio doctrinal en resoluciones TEAC. Distintos de
# los de DGT (cambia el órgano sujeto). Orden NO importa; tomamos el
# del párrafo más al final.
_MARCADORES_CRITERIO_TEAC = (
    re.compile(r"\beste\s+Tribunal\s+(?:Central|Económico)", re.IGNORECASE),
    re.compile(r"\bfija(?:r)?\s+el\s+criterio\b", re.IGNORECASE),
    re.compile(r"\bse\s+fija(?:r)?\s+como\s+criterio\b", re.IGNORECASE),
    re.compile(r"\bestablece(?:r)?\s+como\s+doctrina\b", re.IGNORECASE),
    re.compile(r"\bcriterio\s+(?:de\s+este\s+Tribunal|reiterado|de\s+la\s+Vocal[íi]a)\b", re.IGNORECASE),
    re.compile(r"\bcabe\s+concluir\b", re.IGNORECASE),
    re.compile(r"\ben\s+consecuencia,?\b", re.IGNORECASE),
    re.compile(r"\bpor\s+(?:lo\s+)?tanto,?\b", re.IGNORECASE),
)


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]


def _max_chars(text: str, limit: int = 1500) -> str:
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    cut = truncated.rfind(" ")
    return (truncated[:cut] if cut > 0 else truncated) + " […]"


def extract_criterio(
    plain_text: str,
    *,
    criterio_section: str | None = None,
    fundamentos_section: str | None = None,
) -> str | None:
    """Extrae el criterio doctrinal de la resolución.

    Estrategia:
    1. Si hay `criterio_section` (el HTML del DYCTEA lo marca a veces
       como bloque dedicado), devolverla truncada — es gold.
    2. Buscar marcadores de doctrina en la sección de fundamentos.
    3. Fallback: último párrafo no trivial.
    4. Si nada, `None`.

    Truncado a 1500 chars. `CriterioConfidence.AUTO` por defecto.
    """
    if criterio_section and criterio_section.strip():
        # Quitamos el encabezado "CRITERIO" o "CRITERIO:" si quedó dentro.
        clean = re.sub(
            r"^\s*CRITERIO\s*[:\.]?\s*", "", criterio_section.strip(),
            flags=re.IGNORECASE,
        )
        if clean:
            return _max_chars(clean)

    source = fundamentos_section or plain_text
    if not source.strip():
        return None
    paragraphs = _split_paragraphs(source)
    if not paragraphs:
        return None

    # Recolectamos todos los matches por marcador, tomamos el más al
    # final (suele ser la conclusión).
    candidates: list[tuple[int, str]] = []
    for marcador in _MARCADORES_CRITERIO_TEAC:
        for idx, para in enumerate(paragraphs):
            if marcador.search(para):
                candidates.append((idx, para))
    if candidates:
        candidates.sort(key=lambda x: x[0])
        return _max_chars(candidates[-1][1])

    # Fallback: último párrafo no trivial.
    last = paragraphs[-1] if len(paragraphs[-1]) > 50 else None
    if last is None and len(paragraphs) >= 2:
        last = paragraphs[-2] if len(paragraphs[-2]) > 50 else None
    return _max_chars(last) if last else None
