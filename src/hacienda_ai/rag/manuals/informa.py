"""Parser de FAQs INFORMA de la AEAT.

INFORMA es la base de datos de preguntas frecuentes que la AEAT
mantiene en sede.agenciatributaria.gob.es. Cada FAQ tiene estructura:

    Nº NNNNNN
    Materia: IRPF
    Pregunta: ¿Pueden deducirse los gastos de defensa jurídica…?
    Respuesta: De acuerdo con el artículo 19.2.e) de la Ley 35/2006…
    Normativa: Ley 35/2006 art. 19.2.e)

Las FAQs se descargan manualmente del buscador (no hay API oficial)
y se procesan aquí. Cada FAQ se convierte en 1 `ManualChunk` con
`fuente=INFORMA_FAQ`. No hay chunking por tamaño: las FAQs ya son
breves por construcción, raramente sobrepasan `max_words`.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from ...models import ManualChunk, ManualFuente


class InformaParseError(ValueError):
    """El HTML INFORMA no tiene formato reconocible."""


@dataclass(frozen=True)
class _ParsedFaq:
    """FAQ INFORMA tras parsing, antes de convertirse a ManualChunk."""

    numero: str
    materia: str | None
    pregunta: str
    respuesta: str
    normativa: tuple[str, ...] = field(default_factory=tuple)


# Patrones tolerantes al formato real de INFORMA:
# - Las etiquetas pueden venir en mayúsculas, minúsculas, con o sin tilde.
# - El separador entre etiqueta y valor puede ser `:`, `-`, `→`.
# - El número del FAQ aparece como "Nº NNNNNN" o "Pregunta nº NNNNNN".

_RE_NUMERO = re.compile(
    r"(?:Pregunta\s+)?(?:n[º°.]|num\.?|n[uú]mero)\s*[:.\s]*\s*(?P<num>\d{3,8})",
    re.IGNORECASE,
)
_RE_FIELD = re.compile(
    r"^\s*(?P<label>Materia|Pregunta|Respuesta|Normativa|Hechos)\s*[:\-]\s*",
    re.IGNORECASE | re.MULTILINE,
)

# Conversión HTML → texto, idéntica a la usada por DGT/TEAC.
_RE_TAG = re.compile(r"<[^>]+>")
_RE_ENTITY = re.compile(r"&([a-zA-Z]+|#\d+);")
_RE_WHITESPACE = re.compile(r"[ \t]+")
_RE_NEWLINES = re.compile(r"\n{3,}")
_ENTITY_TABLE = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    "nbsp": " ",
}


def _decode_entity(match: re.Match[str]) -> str:
    entity = match.group(1)
    if entity.startswith("#"):
        try:
            return chr(int(entity[1:]))
        except ValueError:
            return match.group(0)
    return _ENTITY_TABLE.get(entity.lower(), match.group(0))


def _html_to_text(html: str) -> str:
    pre = re.sub(r"<\s*br\s*/?\s*>", "\n", html, flags=re.IGNORECASE)
    pre = re.sub(
        r"</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", pre, flags=re.IGNORECASE
    )
    stripped = _RE_TAG.sub("", pre)
    decoded = _RE_ENTITY.sub(_decode_entity, stripped)
    decoded = _RE_WHITESPACE.sub(" ", decoded)
    lines = [line.strip() for line in decoded.split("\n")]
    joined = "\n".join(lines)
    return _RE_NEWLINES.sub("\n\n", joined).strip()


def _strip_accents(text: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    ).lower()


def parse_informa_html(
    html: str,
    *,
    today: date,
    url_fuente: str | None = None,
) -> list[ManualChunk]:
    """Convierte un HTML del buscador INFORMA en una lista de `ManualChunk`.

    Un HTML puede contener:
    - Una sola FAQ (la página de detalle del buscador).
    - Múltiples FAQs (listado de resultados): el parser detecta cada
      una por su `Nº NNNNNN` y las separa.

    Si el HTML no tiene ningún FAQ identificable, lanzamos
    `InformaParseError`.
    """
    if not html or not html.strip():
        raise InformaParseError("HTML vacío")

    looks_like_html = "<" in html and ">" in html
    plain = _html_to_text(html) if looks_like_html else html.strip()

    faqs = _split_faqs(plain)
    if not faqs:
        raise InformaParseError(
            "no se detectaron FAQs en el HTML "
            "(esperaba secciones con 'Nº NNNNNN', 'Pregunta:', 'Respuesta:')"
        )

    out: list[ManualChunk] = []
    for faq in faqs:
        chunk = _faq_to_chunk(faq, today=today, url_fuente=url_fuente)
        out.append(chunk)
    return out


def _split_faqs(plain: str) -> list[_ParsedFaq]:
    """Separa el texto en bloques por cada `Nº NNNNNN` detectado."""
    # Localizamos las posiciones de los números de FAQ.
    matches = list(_RE_NUMERO.finditer(plain))
    if not matches:
        return []

    blocks: list[tuple[str, int]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(plain)
        block_text = plain[start:end]
        blocks.append((block_text, int(m.group("num"))))

    out: list[_ParsedFaq] = []
    for block_text, numero in blocks:
        faq = _parse_block(block_text, numero)
        if faq is not None:
            out.append(faq)
    return out


def _parse_block(block_text: str, numero: int) -> _ParsedFaq | None:
    """Extrae los campos de un bloque correspondiente a una FAQ."""
    # Localizamos los inicios de cada campo conocido.
    field_matches = list(_RE_FIELD.finditer(block_text))
    if not field_matches:
        return None

    fields: dict[str, str] = {}
    for i, m in enumerate(field_matches):
        label_norm = _strip_accents(m.group("label")).strip()
        value_start = m.end()
        value_end = (
            field_matches[i + 1].start()
            if i + 1 < len(field_matches)
            else len(block_text)
        )
        value = block_text[value_start:value_end].strip()
        fields[label_norm] = value

    pregunta = fields.get("pregunta", "").strip()
    respuesta = fields.get("respuesta", "").strip()
    if not pregunta and not respuesta:
        return None
    if not pregunta:
        pregunta = "[sin pregunta detectada en el HTML]"
    if not respuesta:
        respuesta = "[sin respuesta detectada en el HTML]"

    materia = fields.get("materia") or None
    normativa_raw = fields.get("normativa") or ""
    normativa = _split_normativa(normativa_raw)

    return _ParsedFaq(
        numero=f"{numero:06d}",
        materia=materia,
        pregunta=pregunta,
        respuesta=respuesta,
        normativa=normativa,
    )


def _split_normativa(text: str) -> tuple[str, ...]:
    """Divide el texto del campo `Normativa` en citas individuales."""
    if not text.strip():
        return ()
    # Separadores comunes en INFORMA: ";", "·", saltos de línea.
    parts = re.split(r"[;\n·]+", text)
    return tuple(p.strip() for p in parts if p.strip())


def _faq_to_chunk(
    faq: _ParsedFaq, *, today: date, url_fuente: str | None
) -> ManualChunk:
    """Convierte una FAQ parseada en `ManualChunk`."""
    titulo = f"FAQ INFORMA Nº {faq.numero}"
    if faq.materia:
        titulo = f"{titulo} — {faq.materia}"

    contenido = (
        f"Pregunta: {faq.pregunta}\n\n"
        f"Respuesta: {faq.respuesta}"
    )

    digest = hashlib.sha256(contenido.encode("utf-8")).hexdigest()
    chunk_id = f"{ManualFuente.INFORMA_FAQ.value}::_::faq{faq.numero}::_::_::p1of1"

    return ManualChunk(
        chunk_id=chunk_id,
        fuente=ManualFuente.INFORMA_FAQ,
        ejercicio=None,
        capitulo=None,
        seccion=None,
        subseccion=faq.materia,
        titulo=titulo,
        contenido=contenido,
        page_inicio=None,
        page_fin=None,
        referencias_normativas=faq.normativa,
        url_fuente=url_fuente,
        content_hash=digest,
        last_fetched_at=today,
    )
