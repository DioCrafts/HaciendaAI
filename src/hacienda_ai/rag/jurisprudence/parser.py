"""Parser de HTML CENDOJ → estructura de sentencia.

CENDOJ devuelve un HTML con una cabecera muy regular de metadatos
("Roj:", "ECLI:", "Órgano:", "Fecha:", "Ponente:", etc.) seguida del
texto de la sentencia organizado en secciones: ENCABEZAMIENTO,
ANTECEDENTES DE HECHO, FUNDAMENTOS DE DERECHO y FALLO.

Este parser es defensivo:

- Tolera el HTML real del CGPJ (con muchos `<p>`, `<br>`, `<span>` y
  formato editorial variable) y también texto plano (cuando se sirve
  desde fixtures o archivos descargados como TXT).
- Cabecera: extraída por regex sobre el texto plano normalizado
  (caps-insensitive). Cada campo es opcional.
- Cuerpo: segmenta por encabezados estándar; cualquier sección ausente
  se devuelve como `None`.

La salida es `ParsedSentencia`, una estructura intermedia que aún no es
una `Sentencia` del modelo de dominio: la conversión final la hace el
`runner.py` aplicando los extractores de fallo y ratio decidendi y
añadiendo el hash + el flag de confianza.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date


class SentenciaParseError(ValueError):
    """El HTML no se ha podido convertir en una `ParsedSentencia`."""


@dataclass(frozen=True)
class ParsedSentencia:
    """Estructura intermedia derivada del HTML CENDOJ.

    - `header_fields`: pares clave-valor de la cabecera ("Roj", "ECLI",
      "Órgano", "Sede", "Sección", "Fecha", "Ponente", "Tipo de
      Resolución", "Nº de Recurso", "Nº de Resolución", "Procedimiento").
    - `secciones`: cuerpo segmentado por las secciones estándar.
    - `plain_text`: el texto plano normalizado completo (usado para
      hashing y extracción de fallo/ratio).
    """

    header_fields: dict[str, str] = field(default_factory=dict)
    secciones: dict[str, str] = field(default_factory=dict)
    plain_text: str = ""

    def get_field(self, *aliases: str) -> str | None:
        """Devuelve el primer valor de cabecera que coincida con `aliases`.

        Cada alias se compara normalizando acentos y caso. Útil porque
        CENDOJ a veces usa "Nº" y a veces "N°", "Órgano" vs "Organo".
        """
        normalized = {_norm(k): v for k, v in self.header_fields.items()}
        for alias in aliases:
            value = normalized.get(_norm(alias))
            if value:
                return value
        return None


# ---------- Helpers ----------

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


def _strip_accents(text: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    ).lower()


def _norm(text: str) -> str:
    """Normaliza una clave de cabecera para comparación tolerante."""
    return re.sub(r"\s+", " ", _strip_accents(text)).strip().rstrip(":.,")


def _decode_entity(match: re.Match[str]) -> str:
    entity = match.group(1)
    if entity.startswith("#"):
        try:
            return chr(int(entity[1:]))
        except ValueError:
            return match.group(0)
    return _ENTITY_TABLE.get(entity.lower(), match.group(0))


def html_to_plain_text(html: str) -> str:
    """Convierte HTML CENDOJ a texto plano legible.

    - Sustituye `<br>` y cierres de `<p>`, `<div>` por saltos de línea
      para preservar la estructura visual original.
    - Elimina el resto de tags.
    - Decodifica entidades comunes (`&amp;`, `&nbsp;`, `&#160;`, …).
    - Colapsa espacios horizontales y reduce 3+ saltos de línea a 2.
    """
    # Inserta saltos en cierres de bloques antes de quitar tags.
    pre = re.sub(r"<\s*br\s*/?\s*>", "\n", html, flags=re.IGNORECASE)
    pre = re.sub(r"</\s*(p|div|li|h[1-6])\s*>", "\n", pre, flags=re.IGNORECASE)
    stripped = _RE_TAG.sub("", pre)
    decoded = _RE_ENTITY.sub(_decode_entity, stripped)
    decoded = _RE_WHITESPACE.sub(" ", decoded)
    # Normaliza líneas: trim por línea + colapso de blancos verticales.
    lines = [line.strip() for line in decoded.split("\n")]
    joined = "\n".join(line for line in lines if line != "" or True)  # conserva blancos
    return _RE_NEWLINES.sub("\n\n", joined).strip()


# ---------- Header parsing ----------

# Lista canónica de campos de cabecera que CENDOJ expone, ordenados por
# probabilidad de aparición. La regex captura "Etiqueta: valor" hasta
# fin de línea, tolerando variaciones tipográficas ("Nº" / "N°" / "Núm.").
_HEADER_LABELS = (
    "Roj",
    "Id Cendoj",
    "ECLI",
    "Órgano",
    "Sede",
    "Sección",
    "Fecha",
    "Nº de Recurso",
    "Nº de Resolución",
    "Procedimiento",
    "Ponente",
    "Tipo de Resolución",
    "Materia",
)

# Construimos un patrón que matchee cualquiera de las etiquetas. Para que
# "Nº" y "N°" / "Núm" colapsen, normalizamos las etiquetas con `_norm` al
# extraer, no en el patrón (la regex sobre el texto original conserva la
# etiqueta tal cual aparece).
_HEADER_ALIASES = {
    "n de recurso": "Nº de Recurso",
    "num de recurso": "Nº de Recurso",
    "n. de recurso": "Nº de Recurso",
    "no de recurso": "Nº de Recurso",
    "n de resolucion": "Nº de Resolución",
    "num de resolucion": "Nº de Resolución",
    "n. de resolucion": "Nº de Resolución",
    "no de resolucion": "Nº de Resolución",
    "organo": "Órgano",
    "seccion": "Sección",
    "tipo de resolucion": "Tipo de Resolución",
}


def _canonical_label(raw_label: str) -> str:
    n = _norm(raw_label)
    return _HEADER_ALIASES.get(n, raw_label.strip().rstrip(":."))


_RE_HEADER_LINE = re.compile(
    r"^\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñºª°.\s]{2,40}?)\s*:\s*(.+?)\s*$",
    re.MULTILINE,
)


def parse_header(plain_text: str) -> dict[str, str]:
    """Extrae pares clave-valor de las primeras 80 líneas no vacías.

    Solo consideramos como cabecera los pares cuya clave (tras
    normalizar) está en la lista canónica de campos CENDOJ. Esto evita
    confundir con líneas del cuerpo que llevan ":" (ej. "PRIMERO: ...").
    """
    canonical_keys_norm = {_norm(label) for label in _HEADER_LABELS} | set(
        _HEADER_ALIASES.keys()
    )
    out: dict[str, str] = {}
    lines = plain_text.split("\n")
    # Limitamos a las primeras 80 líneas no vacías; CENDOJ siempre tiene
    # la cabecera al inicio. Esto evita falsos positivos en el cuerpo.
    inspected = 0
    for line in lines:
        if not line.strip():
            continue
        inspected += 1
        if inspected > 80:
            break
        match = _RE_HEADER_LINE.match(line)
        if not match:
            continue
        raw_label, value = match.group(1), match.group(2)
        if _norm(raw_label) in canonical_keys_norm:
            out[_canonical_label(raw_label)] = value.strip()
    return out


# ---------- Body sectioning ----------

# Encabezados estándar que delimitan secciones del cuerpo. Pueden venir
# en mayúsculas, con espacios entre letras ("F A L L O"), o con punto final.
_SECTION_PATTERNS = (
    ("ENCABEZAMIENTO", re.compile(
        r"^\s*E\s*N\s*C\s*A\s*B\s*E\s*Z\s*A\s*M\s*I\s*E\s*N\s*T\s*O\b",
        re.MULTILINE | re.IGNORECASE,
    )),
    ("ANTECEDENTES_DE_HECHO", re.compile(
        r"^\s*ANTECEDENTES\s+DE\s+HECHO\b",
        re.MULTILINE | re.IGNORECASE,
    )),
    ("HECHOS_PROBADOS", re.compile(
        r"^\s*HECHOS\s+PROBADOS\b",
        re.MULTILINE | re.IGNORECASE,
    )),
    ("FUNDAMENTOS_DE_DERECHO", re.compile(
        r"^\s*FUNDAMENTOS\s+DE\s+DERECHO\b",
        re.MULTILINE | re.IGNORECASE,
    )),
    ("FALLO", re.compile(
        r"^\s*F\s*A\s*L\s*L\s*O\b|^\s*FALLAMOS\b|^\s*PARTE\s+DISPOSITIVA\b",
        re.MULTILINE | re.IGNORECASE,
    )),
)


def split_sections(plain_text: str) -> dict[str, str]:
    """Segmenta el texto plano por encabezados estándar.

    Devuelve `{nombre_seccion: cuerpo}` para cada sección detectada. Si
    el HTML no tiene una sección concreta, no aparece en el dict (no se
    devuelve string vacío).

    El cuerpo de cada sección es todo el texto entre su encabezado y el
    siguiente encabezado conocido (o fin de documento).
    """
    # Localizamos posiciones de inicio de cada sección.
    matches: list[tuple[str, int]] = []
    for name, pattern in _SECTION_PATTERNS:
        for m in pattern.finditer(plain_text):
            matches.append((name, m.start()))
    if not matches:
        return {}
    matches.sort(key=lambda x: x[1])

    # Calculamos el final de cada sección como el inicio de la siguiente
    # (o len(plain_text) para la última).
    sections: dict[str, str] = {}
    for i, (name, start) in enumerate(matches):
        end = matches[i + 1][1] if i + 1 < len(matches) else len(plain_text)
        body = plain_text[start:end].strip()
        # Si la sección ya estaba (raro: HTML con múltiples encabezados),
        # nos quedamos con la primera aparición.
        sections.setdefault(name, body)
    return sections


# ---------- Top-level ----------


def parse_sentencia_html(html: str) -> ParsedSentencia:
    """Convierte HTML CENDOJ a `ParsedSentencia`.

    Acepta también texto plano (útil para fixtures): si `html` no tiene
    tags HTML, se procesa directamente como texto.
    """
    if not html or not html.strip():
        raise SentenciaParseError("HTML vacío")

    looks_like_html = "<" in html and ">" in html
    plain = html_to_plain_text(html) if looks_like_html else html.strip()

    header = parse_header(plain)
    sections = split_sections(plain)

    return ParsedSentencia(
        header_fields=header, secciones=sections, plain_text=plain
    )


# ---------- Date parsing helper ----------

_MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

_RE_FECHA_DDMMYYYY = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
_RE_FECHA_LETRA = re.compile(
    r"\b(\d{1,2})\s+de\s+([a-zñáéíóú]+)\s+de\s+(\d{4})\b", re.IGNORECASE
)


def parse_sentencia_date(raw: str) -> date | None:
    """Acepta DD/MM/YYYY o "DD de mes de YYYY". Devuelve None si no parsea."""
    if not raw:
        return None
    m = _RE_FECHA_DDMMYYYY.search(raw)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    m = _RE_FECHA_LETRA.search(raw)
    if m:
        mes_key = _strip_accents(m.group(2))
        if mes_key in _MESES_ES:
            try:
                return date(int(m.group(3)), _MESES_ES[mes_key], int(m.group(1)))
            except ValueError:
                return None
    return None
