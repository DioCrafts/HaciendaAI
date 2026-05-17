"""Parser de HTML del buscador Petete (DGT) → estructura de consulta.

Petete expone las consultas con una cabecera regular ("Núm. Consulta:",
"Fecha Salida:", "Normativa:") seguida de tres bloques canónicos:

- **Descripción de Hechos** — narración del consultante.
- **Cuestión Planteada** — la pregunta concreta.
- **Contestación Completa** — la respuesta de la DGT, donde está el
  criterio doctrinal.

Este parser:

- Tolera el HTML real de Petete (lleno de `<p>`, `<br>`, `<strong>`,
  `<table>` para la cabecera) y también texto plano (para fixtures).
- Cabecera: regex sobre texto plano normalizado, sin distinguir caso
  ni acentos en las claves.
- Cuerpo: segmenta por los encabezados estándar; cada sección ausente
  se devuelve como `None`.

Salida: `ParsedConsulta` — estructura intermedia. La conversión a
`ConsultaDGT` final la hace el `runner.py` aplicando extractores y
calculando el hash.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date


class ConsultaParseError(ValueError):
    """El HTML no se ha podido convertir en una `ParsedConsulta`."""


@dataclass(frozen=True)
class ParsedConsulta:
    """Estructura intermedia derivada del HTML Petete.

    - `header_fields`: pares clave-valor de la cabecera ("Núm. Consulta",
      "Fecha Salida", "Fecha Entrada", "Normativa", "Materia", "Asunto").
    - `secciones`: cuerpo segmentado por encabezados estándar.
    - `plain_text`: el texto plano normalizado completo (para hashing
      y para los extractores).
    """

    header_fields: dict[str, str] = field(default_factory=dict)
    secciones: dict[str, str] = field(default_factory=dict)
    plain_text: str = ""

    def get_field(self, *aliases: str) -> str | None:
        """Busca un campo en `header_fields` con tolerancia a alias.

        Para cada alias intentamos dos lookups: (1) coincidencia directa
        tras normalizar (acentos/puntos/espacios), (2) lookup vía
        `_HEADER_ALIASES` para resolver formas alternativas
        ("Numero de Consulta" → "Núm. Consulta") a la clave canónica
        guardada.
        """
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


# ---------- Helpers de normalización (mismo patrón que cendoj/parser.py) ----------

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
    """Normaliza para comparación tolerante de claves de cabecera.

    Quita acentos, pasa a minúsculas, normaliza puntos y comas internos
    como espacios (para que "Núm. Consulta" colapse a "num consulta",
    igual que "Num Consulta"), colapsa espacios, y elimina los `:`
    finales.
    """
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
    """Convierte HTML Petete a texto plano legible.

    Inserta saltos en cierres de `<p>`, `<div>`, `<br>`, `</tr>` (Petete
    usa tablas para la cabecera). Decodifica entidades comunes. Colapsa
    espacios horizontales y reduce blancos verticales a máximo dos.
    """
    pre = re.sub(r"<\s*br\s*/?\s*>", "\n", html, flags=re.IGNORECASE)
    pre = re.sub(
        r"</\s*(p|div|li|h[1-6]|tr)\s*>", "\n", pre, flags=re.IGNORECASE
    )
    # En las tablas de cabecera, el separador entre celda-clave y
    # celda-valor es </td><td>: insertamos ": " ahí para preservar el
    # par clave-valor en una línea.
    pre = re.sub(r"</\s*td\s*>\s*<\s*td[^>]*>", ": ", pre, flags=re.IGNORECASE)
    pre = re.sub(r"</\s*td\s*>", " ", pre, flags=re.IGNORECASE)
    stripped = _RE_TAG.sub("", pre)
    decoded = _RE_ENTITY.sub(_decode_entity, stripped)
    decoded = _RE_WHITESPACE.sub(" ", decoded)
    lines = [line.strip() for line in decoded.split("\n")]
    joined = "\n".join(line for line in lines if line != "" or True)
    return _RE_NEWLINES.sub("\n\n", joined).strip()


# ---------- Header parsing ----------

# Etiquetas canónicas que Petete expone en la cabecera de cada consulta.
# La detección se hace por normalización (acentos, mayúsculas/minúsculas).
_HEADER_LABELS = (
    "Núm. Consulta",
    "Num. Consulta",
    "Número Consulta",
    "Numero de Consulta",
    "Órgano",
    "Organo SG",
    "Fecha Salida",
    "Fecha de Salida",
    "Fecha Entrada",
    "Fecha de Entrada",
    "Normativa",
    "Materia",
    "Asunto",
    "Cuestión",  # algunas variantes históricas
)

_HEADER_ALIASES = {
    "num consulta": "Núm. Consulta",
    "numero consulta": "Núm. Consulta",
    "numero de consulta": "Núm. Consulta",
    "n consulta": "Núm. Consulta",
    "fecha salida": "Fecha Salida",
    "fecha de salida": "Fecha Salida",
    "fecha entrada": "Fecha Entrada",
    "fecha de entrada": "Fecha Entrada",
    "organo": "Órgano",
    "organo sg": "Órgano",
}


def _canonical_label(raw_label: str) -> str:
    n = _norm(raw_label)
    return _HEADER_ALIASES.get(n, raw_label.strip().rstrip(":."))


_RE_HEADER_LINE = re.compile(
    r"^\s*([A-Za-zÁÉÍÓÚÜÑáéíóúüñ.\s]{3,40}?)\s*:\s*(.+?)\s*$",
    re.MULTILINE,
)


def parse_header(plain_text: str) -> dict[str, str]:
    """Extrae pares clave-valor de las primeras líneas de la consulta.

    Solo guarda claves que (tras normalizar) están en la lista canónica
    de Petete. Esto evita confundir con "PRIMERO:" del cuerpo.
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

# Encabezados canónicos del cuerpo de una consulta Petete.
_SECTION_PATTERNS = (
    ("DESCRIPCION_HECHOS", re.compile(
        r"^\s*Descripci[óo]n\s+(?:de\s+)?(?:los\s+)?[Hh]echos\b[:\.]?",
        re.MULTILINE | re.IGNORECASE,
    )),
    ("CUESTION_PLANTEADA", re.compile(
        r"^\s*Cuesti[óo]n\s+[Pp]lanteada\b[:\.]?",
        re.MULTILINE | re.IGNORECASE,
    )),
    ("CONTESTACION_COMPLETA", re.compile(
        r"^\s*Contestaci[óo]n\s+(?:Completa|Vinculante)?\b[:\.]?",
        re.MULTILINE | re.IGNORECASE,
    )),
)


def split_sections(plain_text: str) -> dict[str, str]:
    """Segmenta el cuerpo por encabezados canónicos.

    Devuelve `{nombre: texto}` para cada sección detectada. Cada sección
    abarca desde su encabezado hasta el inicio de la siguiente (o fin).
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


def parse_consulta_html(html: str) -> ParsedConsulta:
    """Convierte HTML Petete a `ParsedConsulta`.

    Acepta también texto plano (útil para fixtures): si el contenido no
    tiene tags, se procesa directamente como texto.
    """
    if not html or not html.strip():
        raise ConsultaParseError("HTML vacío")

    looks_like_html = "<" in html and ">" in html
    plain = html_to_plain_text(html) if looks_like_html else html.strip()

    header = parse_header(plain)
    sections = split_sections(plain)

    return ParsedConsulta(
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


def parse_consulta_date(raw: str) -> date | None:
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
