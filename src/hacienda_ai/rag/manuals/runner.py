"""Orquestador del pipeline de ingesta de manuales AEAT.

Dos modos:

1. **`ingest_manual_pdf`**: dado un PDF (`Manual Práctico IRPF YYYY.pdf`),
   extrae texto, detecta estructura, chunkea, extrae referencias
   normativas y persiste a `data/manuales/<fuente>/<ejercicio>/...`.

2. **`ingest_informa_html`**: dado un HTML del buscador INFORMA, parsea
   las FAQs y persiste a `data/manuales/informa_faq/undated/...`.

Ambos flujos enriquecen los chunks con `referencias_normativas`
detectadas en el contenido (regex sobre el texto del chunk, mismo
patrón que DGT/TEAC).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ...models import ManualChunk, ManualFuente
from ..dgt.extractors import extract_normativa
from .chunker import ChunkingConfig, chunk_from_structure
from .informa import InformaParseError, parse_informa_html
from .pdf_extractor import PdfExtractor, PdfExtractorError, StubPdfExtractor
from .persistence import PersistedChunk, persist_chunk
from .structure import detect_structure


@dataclass
class IngestionReport:
    """Resumen de una ingesta (manual o INFORMA)."""

    today: date
    chunks: list[ManualChunk] = field(default_factory=list)
    persisted: list[PersistedChunk] = field(default_factory=list)
    error: str | None = None

    @property
    def newly_persisted(self) -> list[PersistedChunk]:
        return [p for p in self.persisted if p.was_new]


def extract_referencias(text: str) -> tuple[str, ...]:
    """Detecta referencias normativas en un texto de chunk.

    Reutiliza el extractor de DGT (mismas regex `Ley X/YYYY art. N`,
    aliases `LIRPF`/`LIS`/`LIVA`…). Importar la función desde aquí
    permite que el chunker enriquezca cada chunk con sus referencias
    sin duplicar regex.
    """
    return extract_normativa(text, None)


def ingest_manual_pdf(
    pdf_path: Path,
    *,
    fuente: ManualFuente,
    ejercicio: int | None,
    today: date,
    root_dir: Path,
    extractor: PdfExtractor | None = None,
    config: ChunkingConfig | None = None,
    url_fuente: str | None = None,
    persist: bool = True,
) -> IngestionReport:
    """Pipeline completo: PDF → chunks → disco.

    `extractor` es inyectable para tests (usar `StubPdfExtractor` con
    fixtures `.txt`). Por defecto se construye un `PypdfExtractor` solo
    cuando se invoca (importación perezosa de `pypdf`).
    """
    report = IngestionReport(today=today)
    if fuente == ManualFuente.INFORMA_FAQ:
        report.error = (
            "ingest_manual_pdf no soporta INFORMA_FAQ; usa ingest_informa_html."
        )
        return report

    if extractor is None:
        # Importación perezosa para no forzar la dependencia `pypdf`
        # cuando el caller usa `StubPdfExtractor`.
        from .pdf_extractor import PypdfExtractor

        extractor = PypdfExtractor()

    try:
        pages = extractor.extract(pdf_path)
    except PdfExtractorError as exc:
        report.error = f"extracción PDF falló: {exc}"
        return report

    if not pages:
        report.error = "el PDF no tiene páginas extraíbles"
        return report

    root = detect_structure(pages)
    chunks = chunk_from_structure(
        root,
        fuente=fuente,
        ejercicio=ejercicio,
        today=today,
        url_fuente=url_fuente,
        config=config,
    )

    # Enriquecemos cada chunk con sus referencias normativas. El chunker
    # produce chunks sin referencias para mantener su responsabilidad
    # acotada; el runner las añade aquí.
    chunks = [_with_referencias(c) for c in chunks]
    report.chunks = chunks

    if persist:
        for chunk in chunks:
            report.persisted.append(persist_chunk(chunk, root=root_dir))
    return report


def ingest_informa_html(
    html_path: Path,
    *,
    today: date,
    root_dir: Path,
    url_fuente: str | None = None,
    persist: bool = True,
) -> IngestionReport:
    """Pipeline completo: HTML INFORMA → chunks → disco.

    Cada FAQ → 1 chunk con `fuente=INFORMA_FAQ`.
    """
    report = IngestionReport(today=today)
    if not html_path.exists():
        report.error = f"fichero no encontrado: {html_path}"
        return report

    try:
        html = html_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        report.error = f"no se pudo decodificar {html_path}: {exc}"
        return report

    try:
        chunks = parse_informa_html(html, today=today, url_fuente=url_fuente)
    except InformaParseError as exc:
        report.error = f"parseo INFORMA falló: {exc}"
        return report

    chunks = [_with_referencias(c) for c in chunks]
    report.chunks = chunks

    if persist:
        for chunk in chunks:
            report.persisted.append(persist_chunk(chunk, root=root_dir))
    return report


def _with_referencias(chunk: ManualChunk) -> ManualChunk:
    """Devuelve un chunk con `referencias_normativas` rellenado.

    Si el chunk ya las trae (caso INFORMA, que las viene en cabecera),
    las preservamos y añadimos solo las nuevas detectadas en el cuerpo.
    """
    detectadas = extract_referencias(chunk.contenido)
    if not detectadas and chunk.referencias_normativas:
        return chunk

    seen: set[str] = set()
    combinadas: list[str] = []
    for cita in list(chunk.referencias_normativas) + list(detectadas):
        key = re.sub(r"\s+", " ", cita).strip().lower()
        if key and key not in seen:
            seen.add(key)
            combinadas.append(cita.strip())

    return ManualChunk(
        chunk_id=chunk.chunk_id,
        fuente=chunk.fuente,
        ejercicio=chunk.ejercicio,
        capitulo=chunk.capitulo,
        seccion=chunk.seccion,
        subseccion=chunk.subseccion,
        titulo=chunk.titulo,
        contenido=chunk.contenido,
        page_inicio=chunk.page_inicio,
        page_fin=chunk.page_fin,
        referencias_normativas=tuple(combinadas),
        url_fuente=chunk.url_fuente,
        content_hash=chunk.content_hash,
        last_fetched_at=chunk.last_fetched_at,
    )


# Pista para el linter: `StubPdfExtractor` se importa para reexport
# desde `__init__.py`; no se usa directamente aquí.
_ = StubPdfExtractor
