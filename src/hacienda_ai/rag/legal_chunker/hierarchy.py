"""Detector de contexto jerárquico (Título/Capítulo/Sección) sobre XML BOE.

El XML consolidado del BOE intercala bloques de estructura y bloques de
precepto:

    <bloque id="t1" tipo="estructura">
      <version>TÍTULO I. SUJECIÓN AL IMPUESTO</version>
    </bloque>
    <bloque id="c1" tipo="estructura">
      <version>CAPÍTULO I. Hecho imponible</version>
    </bloque>
    <bloque id="a6" tipo="precepto">
      <version>Artículo 6. Hecho imponible. ...</version>
    </bloque>
    <bloque id="a7" tipo="precepto">
      <version>Artículo 7. Rentas exentas. ...</version>
    </bloque>
    <bloque id="t2" tipo="estructura">
      <version>TÍTULO II. DETERMINACIÓN DE LA RENTA</version>
    </bloque>
    ...

Al procesar el XML linealmente mantenemos un "estado" de jerarquía: el
último Título visto, el último Capítulo dentro de ese Título, etc. Cada
precepto hereda el estado vigente en su posición.

`iter_structural_blocks` itera TODOS los bloques (estructura + precepto)
en orden de aparición, devolviendo `StructuralBlock` con su tipo. El
caller (`builder.py`) combina con `select_version_for_date` para filtrar
por vigencia y construye los chunks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator


class HierarchyKind(str, Enum):
    """Nivel jerárquico de un bloque de estructura."""

    LIBRO = "libro"
    TITULO = "titulo"
    CAPITULO = "capitulo"
    SECCION = "seccion"
    SUBSECCION = "subseccion"
    OTRO = "otro"


@dataclass(frozen=True)
class StructuralBlock:
    """Un bloque (estructura o precepto) detectado en el XML consolidado.

    Para bloques `tipo="estructura"`, `kind` indica el nivel jerárquico
    y `title` es el texto del encabezado ("TÍTULO I. SUJECIÓN...").
    Para bloques `tipo="precepto"`, `kind` es `None`, `title` es el id
    del bloque ("a6", "a7bis"…) y `precept_body_xml` contiene el cuerpo
    para que el caller le aplique `select_version_for_date`.
    """

    block_id: str
    is_precept: bool
    kind: HierarchyKind | None
    title: str
    raw_body: str  # XML interno del bloque (con `<version>` etc.).


@dataclass(frozen=True)
class HierarchyContext:
    """Contexto jerárquico vigente en un punto del XML."""

    libro: str | None = None
    titulo: str | None = None
    capitulo: str | None = None
    seccion: str | None = None
    subseccion: str | None = None

    def with_block(self, block: StructuralBlock) -> "HierarchyContext":
        """Devuelve un nuevo contexto reflejando la entrada al bloque.

        Reglas:
        - Entrar en un Título resetea Capítulo/Sección/Subsección (todos
          son hijos del Título anterior).
        - Entrar en un Capítulo resetea Sección/Subsección.
        - Entrar en una Sección resetea Subsección.
        - Entrar en una Subsección solo cambia ese campo.
        - Otros niveles (LIBRO o OTRO) actualizan su campo sin tocar
          el resto.
        """
        if block.kind is None:
            return self
        if block.kind == HierarchyKind.LIBRO:
            return HierarchyContext(libro=block.title)
        if block.kind == HierarchyKind.TITULO:
            return HierarchyContext(libro=self.libro, titulo=block.title)
        if block.kind == HierarchyKind.CAPITULO:
            return HierarchyContext(
                libro=self.libro,
                titulo=self.titulo,
                capitulo=block.title,
            )
        if block.kind == HierarchyKind.SECCION:
            return HierarchyContext(
                libro=self.libro,
                titulo=self.titulo,
                capitulo=self.capitulo,
                seccion=block.title,
            )
        if block.kind == HierarchyKind.SUBSECCION:
            return HierarchyContext(
                libro=self.libro,
                titulo=self.titulo,
                capitulo=self.capitulo,
                seccion=self.seccion,
                subseccion=block.title,
            )
        return self

    def as_tuple(self) -> tuple[str, ...]:
        """Vista compacta para metadata: `("Título III", "Capítulo I", …)`.

        Solo incluye niveles presentes (no `None`).
        """
        parts: list[str] = []
        for v in (
            self.libro,
            self.titulo,
            self.capitulo,
            self.seccion,
            self.subseccion,
        ):
            if v is not None:
                parts.append(v)
        return tuple(parts)


# ---------- Regex de detección ----------

# Capturamos cualquier bloque, sin importar el tipo. Necesitamos extraer
# `id`, `tipo` y el body interno para procesarlo después.
_RE_BLOQUE_ANY = re.compile(
    r'<bloque\s+id="(?P<id>[^"]+)"\s+tipo="(?P<tipo>[^"]+)"[^>]*>(?P<body>.*?)</bloque>',
    re.DOTALL,
)

# Detecta el "título" textual de un bloque de estructura desde su
# primer `<version>`. Quitamos tags internos y nos quedamos con la
# primera línea no vacía. Es un mejor-esfuerzo: si el bloque tiene
# múltiples versiones (caso muy raro para títulos/capítulos), tomamos
# la primera.
_RE_FIRST_VERSION_TEXT = re.compile(
    r"<version[^>]*>(.*?)</version>", re.DOTALL
)
_RE_TAGS = re.compile(r"<[^>]+>")
_RE_WHITESPACE = re.compile(r"\s+")


def _extract_structural_title(body: str) -> str:
    """Limpia el body de un bloque estructura para extraer el título.

    BOE pone el texto del título dentro de un `<version><p ...>` o
    similar. Quitamos tags, colapsamos espacios y nos quedamos con la
    primera línea significativa.
    """
    match = _RE_FIRST_VERSION_TEXT.search(body)
    raw = match.group(1) if match else body
    text = _RE_TAGS.sub(" ", raw)
    text = _RE_WHITESPACE.sub(" ", text).strip()
    # Si tiene varias frases, nos quedamos con la primera (típicamente
    # encabezado + descripción separados por punto y aparte). Pero como
    # ya colapsamos saltos, basta con limitar a 200 chars.
    return text[:200]


# Heurística para clasificar el `kind` de un bloque estructura por:
# 1. El prefijo de su `block_id` (los más estables: "l1" libro, "t2"
#    título, "c3" capítulo, "s4" sección, "ss5" subsección).
# 2. Como respaldo, palabras clave al inicio del título textual.
_ID_PREFIX_TO_KIND: tuple[tuple[str, HierarchyKind], ...] = (
    ("ss", HierarchyKind.SUBSECCION),  # subsección antes que sección.
    ("s", HierarchyKind.SECCION),
    ("c", HierarchyKind.CAPITULO),
    ("t", HierarchyKind.TITULO),
    ("l", HierarchyKind.LIBRO),
)

_TITLE_KEYWORDS_TO_KIND: tuple[tuple[str, HierarchyKind], ...] = (
    ("libro", HierarchyKind.LIBRO),
    ("título", HierarchyKind.TITULO),
    ("titulo", HierarchyKind.TITULO),
    ("capítulo", HierarchyKind.CAPITULO),
    ("capitulo", HierarchyKind.CAPITULO),
    ("subsección", HierarchyKind.SUBSECCION),
    ("subseccion", HierarchyKind.SUBSECCION),
    ("sección", HierarchyKind.SECCION),
    ("seccion", HierarchyKind.SECCION),
)


def classify_structural_kind(
    block_id: str, title: str
) -> HierarchyKind:
    """Decide el `HierarchyKind` de un bloque de estructura.

    Política: primero intentamos por prefijo del id (más fiable porque
    es estable); si falla, por keyword en el título textual.
    """
    bid = block_id.lower()
    for prefix, kind in _ID_PREFIX_TO_KIND:
        # Solo aceptamos el prefijo si va seguido de dígitos: "t1",
        # "c2", "ss3"… Evita falsos positivos con ids como "tarifa".
        if bid.startswith(prefix) and len(bid) > len(prefix):
            rest = bid[len(prefix) :]
            if rest[0].isdigit():
                return kind
    lowered_title = title.lower()
    for keyword, kind in _TITLE_KEYWORDS_TO_KIND:
        if lowered_title.startswith(keyword):
            return kind
    return HierarchyKind.OTRO


def iter_structural_blocks(xml: str) -> Iterator[StructuralBlock]:
    """Itera TODOS los bloques del XML en orden de aparición.

    Yielda `StructuralBlock`s. El caller debe ir manteniendo un
    `HierarchyContext` actualizándolo con cada bloque de estructura
    encontrado, y construir chunks cuando aparezca un bloque precepto.
    """
    for match in _RE_BLOQUE_ANY.finditer(xml):
        block_id = match.group("id")
        tipo = match.group("tipo")
        body = match.group("body")
        if tipo == "precepto":
            yield StructuralBlock(
                block_id=block_id,
                is_precept=True,
                kind=None,
                title=block_id,
                raw_body=body,
            )
        else:
            title = _extract_structural_title(body)
            kind = classify_structural_kind(block_id, title)
            yield StructuralBlock(
                block_id=block_id,
                is_precept=False,
                kind=kind,
                title=title,
                raw_body=body,
            )


# Helper que combina iter_structural_blocks con un acumulador de
# `HierarchyContext`. Se usa en `builder.py`.


@dataclass
class _ContextAccumulator:
    """Mantiene el `HierarchyContext` vigente al recorrer los bloques."""

    current: HierarchyContext = field(default_factory=HierarchyContext)

    def absorb(self, block: StructuralBlock) -> None:
        if not block.is_precept:
            self.current = self.current.with_block(block)


def iter_precepts_with_context(
    xml: str,
) -> Iterator[tuple[StructuralBlock, HierarchyContext]]:
    """Itera solo los preceptos, cada uno con su `HierarchyContext` vigente.

    Devuelve `(precept_block, hierarchy_context)`. El contexto es un
    snapshot inmutable del estado jerárquico en el momento del bloque,
    así que el caller puede guardarlo sin riesgo de aliasing.
    """
    accumulator = _ContextAccumulator()
    for block in iter_structural_blocks(xml):
        accumulator.absorb(block)
        if block.is_precept:
            yield block, accumulator.current
