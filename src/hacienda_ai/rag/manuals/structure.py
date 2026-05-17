"""Detector de estructura jerárquica del Manual Práctico AEAT.

Los manuales prácticos siguen una convención editorial estable:

    Capítulo 1. RENDIMIENTOS DEL TRABAJO
    1.1. Concepto
    1.1.1. Definición legal
    [Texto del epígrafe]
    1.1.2. Casos particulares
    [Texto del epígrafe]
    1.2. Cuantificación
    ...

Este módulo escanea el texto plano resultante del extractor PDF y
produce una lista de `StructuralElement` ordenada por aparición. Cada
elemento conserva:

- Su nivel jerárquico (`CAPITULO`, `SECCION`, `SUBSECCION`).
- Su numeración tal como aparece (`1`, `1.1`, `1.1.1`).
- Su título.
- La página donde empieza.
- El texto del cuerpo entre este encabezado y el siguiente.

El chunker (`chunker.py`) consume esta estructura para producir chunks
con metadata jerárquica correcta. Si el manual no encaja en este
formato editorial, el detector devuelve una lista con un único
elemento "raíz" cuyo cuerpo es todo el texto — el chunker entonces
fragmentará por párrafos sin metadata jerárquica.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from .pdf_extractor import PageText


class StructuralElementKind(str, Enum):
    """Nivel jerárquico de un encabezado del manual."""

    ROOT = "root"
    CAPITULO = "capitulo"
    SECCION = "seccion"
    SUBSECCION = "subseccion"


@dataclass
class StructuralElement:
    """Un encabezado detectado en el manual y su cuerpo asociado.

    `body` se llena en `_assign_bodies` después de detectar todos los
    encabezados: cubre el texto entre este encabezado y el inicio del
    siguiente.
    """

    kind: StructuralElementKind
    numbering: str  # "1", "1.1", "1.2.3" o "" para ROOT.
    title: str
    page_start: int | None
    body: str = ""
    children: list["StructuralElement"] = field(default_factory=list)


# ---------- Patrones de encabezado ----------

# Capítulo: "Capítulo N. TÍTULO" o "CAPÍTULO N. TÍTULO". `N` suele ser
# arábigo en manuales modernos; aceptamos también romanos por si acaso.
_RE_CAPITULO = re.compile(
    r"^\s*Cap[íi]tulo\s+(?P<num>\d{1,3}|[IVXLCDM]+)[\.\s—:-]+(?P<title>.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)

# Sección: "1.1. Título" o "1.1 Título" o "1.1.- Título".
# Subsección: "1.1.1. Título".
# Distinguimos por número de puntos en la numeración.
_RE_HEADING_NUMERADO = re.compile(
    r"^\s*(?P<num>\d{1,3}(?:\.\d{1,3}){1,4})\.?\s*[\-—]?\s*(?P<title>[A-ZÁÉÍÓÚÜÑ][^\n]{2,150})\s*$",
    re.MULTILINE,
)

# Filtro: el "título" candidato debe parecerse a un encabezado real, no
# una cita textual. Heurística pragmática: empieza con mayúscula y no
# termina en punto si es una frase larga. Esto es deliberadamente laxo;
# el chunker tolera falsos positivos.


@dataclass(frozen=True)
class _HeadingMatch:
    kind: StructuralElementKind
    numbering: str
    title: str
    absolute_start: int  # offset en el texto concatenado.
    page_number: int


def _kind_from_numbering(num: str) -> StructuralElementKind:
    """Sección si tiene 1 punto interior, subsección si tiene 2 o más."""
    dots = num.count(".")
    if dots == 1:
        return StructuralElementKind.SECCION
    return StructuralElementKind.SUBSECCION


def _flatten_pages(pages: list[PageText]) -> tuple[str, list[tuple[int, int]]]:
    """Concatena páginas en un único texto y devuelve los rangos de offset
    de cada página para poder reconstruir el `page_number` de un match.

    Devuelve `(full_text, [(offset_start, page_number)])`.
    """
    parts: list[str] = []
    offsets: list[tuple[int, int]] = []
    cursor = 0
    for page in pages:
        offsets.append((cursor, page.page_number))
        parts.append(page.text)
        # Separador de página: dos saltos. No usamos `\f` para que las
        # regex no se confundan.
        parts.append("\n\n")
        cursor += len(page.text) + 2
    return "".join(parts), offsets


def _page_at_offset(offset: int, offsets: list[tuple[int, int]]) -> int:
    """Página que contiene `offset` (búsqueda binaria simple)."""
    # offsets está ordenado por offset_start ascendente.
    page = offsets[0][1]
    for start, page_number in offsets:
        if start > offset:
            break
        page = page_number
    return page


def detect_structure(pages: list[PageText]) -> StructuralElement:
    """Construye el árbol jerárquico del manual.

    Devuelve un `StructuralElement` con `kind=ROOT` cuyos `children`
    son los capítulos (o, si no se detectan capítulos, las secciones
    directamente). Cada elemento tiene su `body` con el texto entre
    encabezados.

    Si el manual no tiene encabezados numerados detectables, devuelve
    un ROOT cuyo `body` es todo el texto concatenado: el chunker hará
    fragmentación por párrafos sin metadata jerárquica.
    """
    if not pages:
        return StructuralElement(
            kind=StructuralElementKind.ROOT,
            numbering="",
            title="(vacío)",
            page_start=None,
            body="",
        )

    full_text, offsets = _flatten_pages(pages)

    matches: list[_HeadingMatch] = []
    for m in _RE_CAPITULO.finditer(full_text):
        # Usamos `m.start('num')` (no `m.start()`) para que el `^\s*` inicial
        # no nos haga atribuir el match a la página anterior cuando el
        # encabezado cae justo después de un salto de página.
        head_start = m.start("num")
        matches.append(
            _HeadingMatch(
                kind=StructuralElementKind.CAPITULO,
                numbering=m.group("num"),
                title=m.group("title").strip(),
                absolute_start=head_start,
                page_number=_page_at_offset(head_start, offsets),
            )
        )
    for m in _RE_HEADING_NUMERADO.finditer(full_text):
        num = m.group("num")
        head_start = m.start("num")
        matches.append(
            _HeadingMatch(
                kind=_kind_from_numbering(num),
                numbering=num,
                title=m.group("title").strip(),
                absolute_start=head_start,
                page_number=_page_at_offset(head_start, offsets),
            )
        )

    if not matches:
        # Sin estructura detectable: root único con todo el texto.
        return StructuralElement(
            kind=StructuralElementKind.ROOT,
            numbering="",
            title="(sin estructura jerárquica detectada)",
            page_start=pages[0].page_number,
            body=full_text.strip(),
        )

    matches.sort(key=lambda m: m.absolute_start)

    # Construimos los `StructuralElement` y les asignamos `body` con el
    # texto entre este encabezado y el siguiente.
    elements: list[StructuralElement] = []
    for i, match in enumerate(matches):
        body_start = match.absolute_start + len(match.title)
        body_end = (
            matches[i + 1].absolute_start
            if i + 1 < len(matches)
            else len(full_text)
        )
        body = full_text[body_start:body_end].strip()
        elements.append(
            StructuralElement(
                kind=match.kind,
                numbering=match.numbering,
                title=match.title,
                page_start=match.page_number,
                body=body,
            )
        )

    # Anidamos en árbol jerárquico: capítulo > sección > subsección.
    root = StructuralElement(
        kind=StructuralElementKind.ROOT,
        numbering="",
        title="ROOT",
        page_start=pages[0].page_number,
        body="",
    )
    stack: list[StructuralElement] = [root]
    rank = {
        StructuralElementKind.ROOT: 0,
        StructuralElementKind.CAPITULO: 1,
        StructuralElementKind.SECCION: 2,
        StructuralElementKind.SUBSECCION: 3,
    }
    for elem in elements:
        # Desapilamos hasta encontrar un padre de rango estrictamente menor.
        while stack and rank[stack[-1].kind] >= rank[elem.kind]:
            stack.pop()
        if not stack:
            stack.append(root)
        stack[-1].children.append(elem)
        stack.append(elem)
    return root


AncestorsByKind = dict[StructuralElementKind, "StructuralElement | None"]


def iter_leaves(
    root: StructuralElement,
) -> list[tuple[StructuralElement, AncestorsByKind]]:
    """Devuelve `(leaf, ancestors)` por cada hoja efectiva del árbol.

    Una hoja es:
    - Un elemento sin hijos (típicamente una subsección).
    - O un preámbulo sintético cuando un nivel intermedio (capítulo /
      sección) tiene texto antes de su primer hijo. Sin esto se
      perdería el cuerpo del preámbulo.

    `ancestors` ya viene calculado durante la travesía. Esto permite al
    chunker conocer la jerarquía completa de cada hoja sin tener que
    rebuscar en el árbol — y resuelve el caso del preámbulo sintético,
    que no está físicamente en el árbol pero hereda los ancestros del
    nodo que lo originó.
    """
    leaves: list[tuple[StructuralElement, AncestorsByKind]] = []
    initial: AncestorsByKind = {
        StructuralElementKind.CAPITULO: None,
        StructuralElementKind.SECCION: None,
        StructuralElementKind.SUBSECCION: None,
    }
    _collect_leaves(root, initial, leaves)
    return leaves


def _collect_leaves(
    node: StructuralElement,
    ancestors: AncestorsByKind,
    out: list[tuple[StructuralElement, AncestorsByKind]],
) -> None:
    # Hoja real: nodo sin hijos.
    if not node.children:
        if node.kind != StructuralElementKind.ROOT or node.body:
            out.append((node, dict(ancestors)))
        return

    # Preámbulo sintético: el nodo intermedio tiene texto propio antes
    # de su primer hijo. Emitimos una hoja sintética conservando la
    # jerarquía actual (los ancestros del propio nodo, no los del
    # sintético, que no tiene posición en el árbol).
    if node.body.strip():
        synthetic = StructuralElement(
            kind=node.kind,
            numbering=node.numbering,
            title=node.title + " (preámbulo)",
            page_start=node.page_start,
            body=node.body,
        )
        out.append((synthetic, dict(ancestors)))

    # Bajamos un nivel: actualizamos `ancestors` con el nodo actual si
    # encaja en algún slot conocido.
    child_ancestors = dict(ancestors)
    if node.kind in child_ancestors:
        child_ancestors[node.kind] = node
    for child in node.children:
        _collect_leaves(child, child_ancestors, out)
