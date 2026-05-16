"""Ingesta de manuales AEAT y FAQs INFORMA: PDFs oficiales + parser + chunking.

La AEAT publica anualmente manuales prácticos (IRPF, IS, IVA…) con su
doctrina operativa y mantiene el servicio INFORMA con miles de FAQs.
Este pipeline:

1. Extrae texto de los PDFs (Manual Práctico) con backend pluggable
   (`pypdf` por defecto; `pdfplumber`/`pymupdf` sustituibles vía
   `PdfExtractor` Protocol).
2. Detecta la estructura jerárquica (capítulos, secciones, subsecciones).
3. Aplica chunking semántico respetando esa jerarquía, con tamaños
   acotados para encajar en ventanas de embedding.
4. Para INFORMA, parser HTML simple (cada FAQ → 1 chunk).
5. Persiste chunks en `data/manuales/<fuente>/<ejercicio>/<chunk_id>.json`.

A diferencia del resto de pipelines, NO hay heurística de extracción
de criterio: el contenido del manual ES el criterio AEAT verbatim. El
chunker solo trocea con metadata jerárquica.

Cliente local-only por ahora: los PDFs se descargan manualmente desde
sede.agenciatributaria.gob.es y se ingieren con `--pdf path/al/manual.pdf`.
"""

from __future__ import annotations

from .chunker import (
    ChunkingConfig,
    chunk_from_structure,
)
from .informa import (
    InformaParseError,
    parse_informa_html,
)
from .pdf_extractor import (
    PageText,
    PdfExtractor,
    PdfExtractorError,
    PypdfExtractor,
    StubPdfExtractor,
)
from .persistence import (
    PersistedChunk,
    chunk_path,
    load_chunk,
    persist_chunk,
)
from .runner import (
    IngestionReport,
    extract_referencias,
    ingest_informa_html,
    ingest_manual_pdf,
)
from .structure import (
    StructuralElement,
    StructuralElementKind,
    detect_structure,
)

__all__ = [
    "ChunkingConfig",
    "InformaParseError",
    "IngestionReport",
    "PageText",
    "PdfExtractor",
    "PdfExtractorError",
    "PersistedChunk",
    "PypdfExtractor",
    "StructuralElement",
    "StructuralElementKind",
    "StubPdfExtractor",
    "chunk_from_structure",
    "chunk_path",
    "detect_structure",
    "extract_referencias",
    "ingest_informa_html",
    "ingest_manual_pdf",
    "load_chunk",
    "parse_informa_html",
    "persist_chunk",
]
