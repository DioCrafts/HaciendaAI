"""Parser de HTML del buscador DYCTEA → estructura de resolución TEAC/TEAR.

El buscador DYCTEA expone las resoluciones con una cabecera de
metadatos ("Nº Resolución:", "Fecha:", "Órgano:", "Materia:", "Criterio:")
seguida del texto completo de la resolución.

Estructura típica:
- **Cabecera**: tabla o lista de pares clave-valor.
- **Criterio**: párrafo (a veces ya destacado por el TEAC en el HTML)
  con la doctrina sintetizada. Útil porque ya viene marcado en muchos
  casos.
- **Resolución**: el texto completo (antecedentes, fundamentos,
  resolución).
- **Fallo / Por todo lo expuesto**: la decisión final.

Este parser:
- Tolera el HTML real con tablas, divs y formato editorial variable, y
  también texto plano (fixtures).
- Cabecera por regex sobre texto plano normalizado.
- Cuerpo segmentado por encabezados conocidos.

Salida: `ParsedResolucion` — estructura intermedia que el runner
convierte a `ResolucionTEAC` aplicando extractores.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date


class ResolucionParseError(ValueError):
    """El HTML no se ha podido convertir en `ParsedResolucion`."""


@dataclass(frozen=True)
class ParsedResolucion:
    """Estructura intermedia derivada del HTML DYCTEA."""

    header_fields: dict[str, str] = field(default_factory=dict)
    secciones: dict[str, str] = field(default_factory=dict)
    plain_text: str = ""

    def get_field(self, *aliases: str) -> str | None:
        """Lookup tolerante con alias canónicos (igual patrón que DGT)."""
        normalized = {_norm(k): v for k, v in self.header_fields.items()}
        for alias in aliases:
            key = _norm(alias)
            value = normalized.get(key)
            if value:
                return value
            canonical = _HEADER_ALIASES.get(key)
            if canonical is not None:
                value = normalized.get(_norm(canonical))
                if value:
                    return value
        return None


# ---------- Helpers de normalización ----------

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
    """Normaliza para comparación tolerante: acentos, puntos y `:`."""
    out = _strip_accents(text)
    out = re.sub(r"[.,]", " ", out)
    out = re.sub(r"\s+", " ", out).strip().rstrip(":")
    return out


def _decode_entity(match: re.Match[str]) -> str:
    entity = match.group(1)
    if entity.startswith("#"):
        try:
            return chr(int(entity[1:]))
        except ValueError:
            return match.group(0)
    return _ENTITY_TABLE.get(entity.lower(), match.group(0))


def html_to_plain_text(html: str) -> str:
    """Convierte HTML DYCTEA a texto plano legible.

    Preserva pares clave-valor de tablas (`</td><td>` → `: `), inserta
    saltos en cierres de bloques, decodifica entidades, colapsa
    whitespace.
    """
    pre = re.sub(r"<\s*br\s*/?\s*>", "\n", html, flags=re.IGNORECASE)
    pre = re.sub(
        r"</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", pre, flags=re.IGNORECASE
    )
    pre = re.sub(r"</\s*td\s*>\s*<\s*td[^>]*>", ": ", pre, flags=re.IGNORECASE)
    pre = re.sub(r"</\s*td\s*>", " ", pre, flags=re.IGNORECASE)
    stripped = _RE_TAG.sub("", pre)
    decoded = _RE_ENTITY.sub(_decode_entity, stripped)
    decoded = _RE_WHITESPACE.sub(" ", decoded)
    lines = [line.strip() for line in decoded.split("\n")]
    joined = "\n".join(line for line in lines if line != "" or True)
    return _RE_NEWLINES.sub("\n\n", joined).strip()


# ---------- Header parsing ----------

# Etiquetas canónicas que DYCTEA expone en la cabecera. La detección se
# hace por `_norm` tolerante. Los alias mapean variantes históricas a la
# etiqueta canónica.
_HEADER_LABELS = (
    "Nº Resolución",
    "Numero de Resolucion",
    "Nº de Resolución",
    "Fecha",
    "Órgano",
    "Sala",
    "Sede",
    "Unidad Resolutoria",
    "Materia",
    "Concepto",
    "Tipo de Resolución",
    "Criterio",
    "Asunto",
    "Normativa",
    "Reclamante",
)

_HEADER_ALIASES = {
    "n resolucion": "Nº Resolución",
    "n de resolucion": "Nº Resolución",
    "no de resolucion": "Nº Resolución",
    "numero de resolucion": "Nº Resolución",
    "numero resolucion": "Nº Resolución",
    "n reclamacion": "Nº Resolución",
    "numero reclamacion": "Nº Resolución",
    "fecha resolucion": "Fecha",
    "fecha de resolucion": "Fecha",
    "organo": "Órgano",
    "unidad resolutoria": "Unidad Resolutoria",
    "tipo resolucion": "Tipo de Resolución",
    "tipo de resolucion": "Tipo de Resolución",
}


def _canonical_label(raw_label: str) -> str:
    n = _norm(raw_label)
    return _HEADER_ALIASES.get(n, raw_label.strip().rstrip(":."))


_RE_HEADER_LINE = re.compile(
    # Aceptamos º, ª, ° en la etiqueta (CENDOJ y DYCTEA usan "Nº").
    r"^\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñºª°.\s]{2,40}?)\s*:\s*(.+?)\s*$",
    re.MULTILINE,
)


def parse_header(plain_text: str) -> dict[str, str]:
    """Extrae pares clave-valor de la cabecera de la resolución.

    Solo acepta como cabecera líneas cuya clave normalizada está en la
    lista canónica TEAC. Esto evita que "PRIMERO:" o "FUNDAMENTOS:" del
    cuerpo contaminen el header.
    """
    canonical_keys_norm = {_norm(label) for label in _HEADER_LABELS} | set(
        _HEADER_ALIASES.keys()
    )
    out: dict[str, str] = {}
    lines = plain_text.split("\n")
    inspected = 0
    for line in lines:
        if not line.strip():
            continue
        inspected += 1
        # Cabecera TEAC suele caber en las primeras 60 líneas. Más
        # generoso que en DGT porque hay TEAC con cabeceras largas
        # (varios sucesivos campos administrativos).
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

# Encabezados canónicos del cuerpo de una resolución TEAC.
_SECTION_PATTERNS = (
    ("ANTECEDENTES", re.compile(
        r"^\s*ANTECEDENTES(?:\s+DE\s+HECHO)?\b[:\.]?",
        re.MULTILINE | re.IGNORECASE,
    )),
    ("FUNDAMENTOS", re.compile(
        r"^\s*FUNDAMENTOS\s+(?:DE\s+DERECHO|JUR[ÍI]DICOS)\b[:\.]?",
        re.MULTILINE | re.IGNORECASE,
    )),
    ("CRITERIO", re.compile(
        r"^\s*CRITERIO\b[:\.]?",
        re.MULTILINE | re.IGNORECASE,
    )),
    ("FALLO", re.compile(
        r"^\s*(?:F\s*A\s*L\s*L\s*O|"
        r"POR\s+TODO\s+LO\s+EXPUESTO|"
        r"EN\s+SU\s+VIRTUD|"
        r"ACUERDA|"
        r"PARTE\s+DISPOSITIVA|"
        r"RESUELVE)\b[:\.]?",
        re.MULTILINE | re.IGNORECASE,
    )),
)


def split_sections(plain_text: str) -> dict[str, str]:
    """Segmenta el texto plano por encabezados canónicos TEAC.

    Devuelve `{nombre_seccion: cuerpo}` para cada sección detectada. Si
    falta una sección, no aparece en el dict.
    """
    matches: list[tuple[str, int]] = []
    for name, pattern in _SECTION_PATTERNS:
        for m in pattern.finditer(plain_text):
            matches.append((name, m.start()))
    if not matches:
        return {}
    matches.sort(key=lambda x: x[1])

    sections: dict[str, str] = {}
    for i, (name, start) in enumerate(matches):
        end = matches[i + 1][1] if i + 1 < len(matches) else len(plain_text)
        body = plain_text[start:end].strip()
        sections.setdefault(name, body)
    return sections


# ---------- Top-level ----------


def parse_resolucion_html(html: str) -> ParsedResolucion:
    """Convierte HTML DYCTEA a `ParsedResolucion`.

    Acepta también texto plano (útil para fixtures): si el contenido no
    tiene tags, se procesa directamente como texto.
    """
    if not html or not html.strip():
        raise ResolucionParseError("HTML vacío")

    looks_like_html = "<" in html and ">" in html
    plain = html_to_plain_text(html) if looks_like_html else html.strip()

    header = parse_header(plain)
    sections = split_sections(plain)

    return ParsedResolucion(
        header_fields=header, secciones=sections, plain_text=plain
    )


# ---------- Date parsing ----------

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


def parse_resolucion_date(raw: str) -> date | None:
    """Acepta DD/MM/YYYY o "DD de mes de YYYY". `None` si no parsea."""
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
