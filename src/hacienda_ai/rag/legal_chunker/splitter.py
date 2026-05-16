"""Divisor de artículos en apartados numerados/letrados.

Los artículos del BOE se estructuran típicamente como:

    Artículo 19. Gastos deducibles.

    1. El rendimiento neto del trabajo será el resultado de disminuir
       el rendimiento íntegro en el importe de los gastos deducibles.

    2. Tendrán la consideración de gastos deducibles exclusivamente
       los siguientes:

       a) Las cotizaciones a la Seguridad Social…
       b) Las detracciones por derechos pasivos.
       c) Las cotizaciones a colegios de huérfanos…
       d) Las cuotas satisfechas a sindicatos…
       e) Los gastos de defensa jurídica derivados directamente de
          litigios suscitados en la relación del contribuyente con la
          persona de la que percibe los rendimientos.

Para el RAG queremos chunks por apartado (`art. 19.2.e)`) porque:

- La cita pinpoint exige granularidad apartado/letra.
- Un apartado es semánticamente cohesivo y cabe en una ventana de
  embedding holgada.
- Los apartados a menudo cambian independientemente: indexar por
  apartado facilita invalidación selectiva cuando la norma se modifica.

Este módulo parte texto plano (ya normalizado por
`articles.normalize_version_text`) y produce una lista de `Apartado`s.
Si el artículo no tiene numeración explícita (artículo corto de un solo
párrafo), devuelve un único Apartado con `numero=None`.

Las letras dentro de un apartado se devuelven como `Apartado`s
separados con `numero="2.e)"` para preservar la pinpoint canónica.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Apartado:
    """Apartado individual de un artículo.

    `numero` es el identificador canónico: "1", "2", "2.a)", "2.e)"…
    `None` cuando el artículo no tiene apartados numerados (texto
    cohesivo en un solo bloque).
    """

    numero: str | None
    texto: str


# Línea que empieza un apartado numerado: "1.", "2.", "10.".
# Aceptamos "1.-", "1 -", "1)". El BOE consolidado usa típicamente
# "1.", "2."... al inicio de párrafo.
_RE_APARTADO_NUMERO = re.compile(
    r"^\s*(?P<num>\d{1,3})[\.\)]\s",
    re.MULTILINE,
)

# Línea que empieza una letra dentro de un apartado: "a)", "b)", "c)".
# Algunos manuales y RD usan letras mayúsculas; aceptamos las dos.
_RE_APARTADO_LETRA = re.compile(
    r"^\s*(?P<letra>[a-zñ])\)\s",
    re.MULTILINE | re.IGNORECASE,
)


def _strip_article_heading(text: str) -> str:
    """Quita el encabezado del artículo si está presente.

    Patrón: "Artículo N. Título." al inicio. Devolvemos el texto del
    cuerpo del artículo, ya sin el encabezado, para no contaminar el
    primer apartado con el título.
    """
    pattern = re.compile(
        r"^\s*Art[íi]culo\s+\d+[a-z]*\.?\s*[^\n.]*\.\s*", re.IGNORECASE
    )
    match = pattern.match(text)
    if match:
        return text[match.end() :].strip()
    return text.strip()


_RE_LETRA_INLINE = re.compile(
    r"(?<=[.:;])\s+(?=[a-zñ]\)\s)", re.IGNORECASE
)
_RE_NUMERO_INLINE = re.compile(
    r"(?<=[.:;])\s+(?=\d{1,3}[.\)]\s)"
)


def _normalize_marker_positions(text: str) -> str:
    """Inserta saltos de línea antes de marcadores `a)` o `1.` inline.

    BOE concatena a menudo apartados/letras en un solo párrafo
    ("Tendrán la consideración: a) X. b) Y. e) Z."). Forzar saltos
    delante de cada marcador permite a las regex con `^\\s*MARCADOR`
    detectarlos.
    """
    text = _RE_LETRA_INLINE.sub("\n\n", text)
    text = _RE_NUMERO_INLINE.sub("\n\n", text)
    return text


def split_article_into_apartados(article_text: str) -> list[Apartado]:
    """Divide el texto plano de un artículo en lista de `Apartado`s.

    Estrategia:
    1. Si el artículo no tiene marcadores `\\d+\\.`, devolver un único
       apartado con `numero=None` y el texto entero.
    2. Si tiene marcadores numéricos, dividir por ellos.
    3. Dentro de cada apartado, si hay letras `a)`, `b)`, ..., subdividir
       en sub-apartados con `numero="N.letra)"`. La cabecera del
       apartado (el texto previo a la primera letra) NO se descarta:
       se emite como `Apartado(numero="N", ...)`.
    4. Si todo el artículo va solo con letras (raro en LIRPF, posible
       en disposiciones adicionales), emitir las letras directamente
       con `numero="letra)"`.

    Antes de las regex de marcadores aplicamos
    `_normalize_marker_positions` para forzar saltos de línea delante
    de letras/números que vienen inline (caso típico del BOE real).
    """
    body = _strip_article_heading(article_text)
    if not body:
        return []

    body = _normalize_marker_positions(body)
    numero_matches = list(_RE_APARTADO_NUMERO.finditer(body))
    if not numero_matches:
        # Sin apartados numéricos. ¿Hay letras?
        letras = _split_by_letras(body, numero_padre=None)
        if letras:
            return letras
        # Solo un párrafo. Devolvemos un único apartado sin número.
        return [Apartado(numero=None, texto=body)]

    apartados: list[Apartado] = []
    for i, m in enumerate(numero_matches):
        numero = m.group("num")
        start = m.start()
        end = (
            numero_matches[i + 1].start()
            if i + 1 < len(numero_matches)
            else len(body)
        )
        # Quitamos el propio número del inicio para no duplicarlo en
        # el texto del apartado: la metadata `numero` ya lo lleva.
        chunk_text = body[start:end].strip()
        chunk_text = _RE_APARTADO_NUMERO.sub("", chunk_text, count=1).strip()

        sub_letras = _split_by_letras(chunk_text, numero_padre=numero)
        if sub_letras:
            apartados.extend(sub_letras)
        else:
            apartados.append(Apartado(numero=numero, texto=chunk_text))
    return apartados


def _split_by_letras(text: str, *, numero_padre: str | None) -> list[Apartado]:
    """Divide un texto por sus letras `a)`, `b)`, `c)`...

    Si encuentra letras, devuelve una lista de Apartados con
    `numero="N.letra)"` (si hay padre) o `numero="letra)"` (si no).
    Además, si el texto tiene preámbulo antes de la primera letra, lo
    emite como Apartado padre primero (típico: "2. Tendrán la
    consideración de gastos: a) ... b) ...").

    Devuelve `[]` si no hay letras detectables.
    """
    matches = list(_RE_APARTADO_LETRA.finditer(text))
    if not matches:
        return []

    apartados: list[Apartado] = []

    # Preámbulo: texto antes de la primera letra.
    preamble = text[: matches[0].start()].strip()
    if preamble and numero_padre is not None:
        apartados.append(Apartado(numero=numero_padre, texto=preamble))

    for i, m in enumerate(matches):
        letra = m.group("letra").lower()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk_text = text[start:end].strip()
        # Quitamos el propio "a)" del inicio para no duplicar.
        chunk_text = _RE_APARTADO_LETRA.sub("", chunk_text, count=1).strip()
        if numero_padre is not None:
            numero = f"{numero_padre}.{letra})"
        else:
            numero = f"{letra})"
        apartados.append(Apartado(numero=numero, texto=chunk_text))
    return apartados
