"""Chunking semántico de manuales AEAT.

A diferencia del chunking por tokens ciegos, este módulo respeta la
estructura jerárquica detectada por `structure.py`:

- Cada hoja (subsección) genera 1 o más chunks dependiendo de su
  tamaño.
- Si la hoja es pequeña (≤ `max_words`), 1 chunk.
- Si es grande, se subdivide por párrafos en chunks de tamaño objetivo
  `target_words`, sin cortar párrafos a la mitad.
- Si un párrafo individual excede `max_words`, se acepta tal cual: es
  preferible un chunk grande coherente a uno cortado a mitad de frase.
- Cada chunk hereda toda la metadata jerárquica (capítulo, sección,
  subsección) de su hoja origen, más el rango de páginas.

Tamaños sensatos para embeddings comunes (Voyage law-2, OpenAI
ada-002, etc.):
- `min_words=50`: por debajo de esto el chunk es probable que aporte
  poco contexto.
- `target_words=400`: cabe holgadamente en ventanas de 512-1024 tokens.
- `max_words=800`: límite duro; un párrafo más grande se acepta entero
  pero se marca para revisión.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable

from ...models import ManualChunk, ManualFuente
from .structure import (
    StructuralElement,
    StructuralElementKind,
    iter_leaves,
)


@dataclass(frozen=True)
class ChunkingConfig:
    """Parámetros del chunker.

    `min_words` filtra chunks demasiado triviales (página en blanco,
    encabezados aislados). `target_words` es el tamaño deseado de cada
    chunk. `max_words` es el umbral a partir del cual una hoja grande
    se subdivide.
    """

    min_words: int = 50
    target_words: int = 400
    max_words: int = 800


def chunk_from_structure(
    root: StructuralElement,
    *,
    fuente: ManualFuente,
    ejercicio: int | None,
    today: date,
    url_fuente: str | None = None,
    config: ChunkingConfig | None = None,
) -> list[ManualChunk]:
    """Aplica chunking semántico al árbol estructural y devuelve la lista de chunks.

    `fuente`, `ejercicio` y `url_fuente` se propagan a cada chunk. Los
    `chunk_id` son estables: la misma posición en la misma jerarquía da
    el mismo id entre ejecuciones.
    """
    cfg = config or ChunkingConfig()
    out: list[ManualChunk] = []
    # Numeramos las hojas globalmente para garantizar `chunk_id` único
    # incluso entre preámbulos sintéticos con la misma jerarquía.
    for leaf_index, (leaf, ancestors) in enumerate(iter_leaves(root)):
        capitulo = ancestors[StructuralElementKind.CAPITULO]
        seccion = ancestors[StructuralElementKind.SECCION]
        subseccion = ancestors[StructuralElementKind.SUBSECCION]

        # Si la propia hoja es de tipo SUBSECCION, ella misma actúa
        # como subsección en la metadata del chunk.
        leaf_as_subseccion = (
            leaf if leaf.kind == StructuralElementKind.SUBSECCION else subseccion
        )

        leaf_chunks = list(_split_leaf(leaf, cfg))
        for part_idx, chunk_text in enumerate(leaf_chunks, start=1):
            chunk_id = _chunk_id(
                fuente=fuente,
                ejercicio=ejercicio,
                capitulo=capitulo,
                seccion=seccion,
                subseccion=leaf_as_subseccion,
                leaf=leaf,
                leaf_index=leaf_index,
                part=part_idx,
                total_parts=len(leaf_chunks),
            )
            out.append(
                ManualChunk(
                    chunk_id=chunk_id,
                    fuente=fuente,
                    ejercicio=ejercicio,
                    capitulo=_label(capitulo),
                    seccion=_label(seccion),
                    subseccion=_label(leaf_as_subseccion),
                    titulo=_chunk_title(leaf, part_idx, len(leaf_chunks)),
                    contenido=chunk_text,
                    page_inicio=leaf.page_start,
                    # Aproximación: la hoja sintética puede abarcar varias
                    # páginas si su `body` es largo, pero no rastreamos el
                    # offset final. Mejorable pasando offsets de página
                    # al chunker.
                    page_fin=leaf.page_start,
                    referencias_normativas=(),
                    url_fuente=url_fuente,
                    content_hash=hashlib.sha256(
                        chunk_text.encode("utf-8")
                    ).hexdigest(),
                    last_fetched_at=today,
                )
            )
    return out


def _split_leaf(
    leaf: StructuralElement, cfg: ChunkingConfig
) -> Iterable[str]:
    """Divide el cuerpo de una hoja en chunks respetando párrafos.

    Yields strings de chunk. La estrategia:
    1. Partir por párrafos (líneas en blanco).
    2. Acumular párrafos hasta alcanzar `target_words`.
    3. Si añadir el siguiente párrafo pasaría de `max_words`, emitir lo
       acumulado y empezar de nuevo.
    4. Si el cuerpo entero es muy pequeño (< `min_words`), emitir igual:
       el caller decide si filtrarlo (no lo hacemos aquí porque a veces
       una hoja corta es exactamente la doctrina que queremos —
       e.g. "Tipos de gravamen IVA").
    """
    paragraphs = _split_paragraphs(leaf.body)
    if not paragraphs:
        return

    buffer: list[str] = []
    buffer_words = 0
    for para in paragraphs:
        para_words = _count_words(para)
        # Caso: el párrafo en sí mismo es enorme. Lo emitimos solo para
        # no romper su unidad semántica, aunque exceda `max_words`.
        if para_words > cfg.max_words and not buffer:
            yield para
            continue
        # Caso: añadir el párrafo desborda `max_words`. Emitimos el
        # buffer y empezamos uno nuevo con este párrafo.
        if buffer and buffer_words + para_words > cfg.max_words:
            yield "\n\n".join(buffer)
            buffer = [para]
            buffer_words = para_words
            continue
        buffer.append(para)
        buffer_words += para_words
        # Si llegamos al objetivo, emitimos y reiniciamos. Esto produce
        # chunks ligeramente más pequeños que `max_words` típicamente.
        if buffer_words >= cfg.target_words:
            yield "\n\n".join(buffer)
            buffer = []
            buffer_words = 0
    if buffer:
        yield "\n\n".join(buffer)


_RE_PARA_SPLIT = re.compile(r"\n\s*\n+")


def _split_paragraphs(text: str) -> list[str]:
    return [p.strip() for p in _RE_PARA_SPLIT.split(text) if p.strip()]


_RE_WORD = re.compile(r"\S+")


def _count_words(text: str) -> int:
    return len(_RE_WORD.findall(text))


def _label(elem: StructuralElement | None) -> str | None:
    """Etiqueta humana de un elemento estructural: 'Capítulo 3' / '3.2' / etc."""
    if elem is None or elem.kind == StructuralElementKind.ROOT:
        return None
    if elem.kind == StructuralElementKind.CAPITULO:
        return f"Capítulo {elem.numbering}. {elem.title}"
    return f"{elem.numbering}. {elem.title}"


def _chunk_id(
    *,
    fuente: ManualFuente,
    ejercicio: int | None,
    capitulo: StructuralElement | None,
    seccion: StructuralElement | None,
    subseccion: StructuralElement | None,
    leaf: StructuralElement,
    leaf_index: int,
    part: int,
    total_parts: int,
) -> str:
    """Id estable y único del chunk.

    Formato:
        <fuente>::<ejercicio_o_>::cap<NUM_o_>::sec<NUM_o_>::sub<NUM_o_>::leaf<N>::p<idx>of<T>

    Cuando un segmento no aplica usamos `_`. `leaf<N>` distingue entre
    preámbulos sintéticos de distintos niveles que comparten la misma
    jerarquía explícita (capítulo/sección/subsección): sin él, los
    preámbulos de "Capítulo 1" y "Sección 1.1" colisionarían porque
    ambos tienen `cap_::sec_::sub_`.
    """

    def _seg(prefix: str, elem: StructuralElement | None) -> str:
        if elem is None or elem.kind == StructuralElementKind.ROOT:
            return f"{prefix}_"
        safe = elem.numbering.replace(".", "_") if elem.numbering else "_"
        return f"{prefix}{safe}"

    eje = str(ejercicio) if ejercicio is not None else "_"
    return (
        f"{fuente.value}::{eje}::"
        f"{_seg('cap', capitulo)}::{_seg('sec', seccion)}::"
        f"{_seg('sub', subseccion)}::leaf{leaf_index}::p{part}of{total_parts}"
    )


def _chunk_title(
    leaf: StructuralElement, part: int, total: int
) -> str:
    """Título legible: numeración + título de la hoja + indicador de parte si aplica."""
    base = (
        f"{leaf.numbering}. {leaf.title}"
        if leaf.numbering
        else leaf.title
    )
    if total > 1:
        return f"{base} (parte {part}/{total})"
    return base
