"""Extracción de texto plano desde PDFs de manuales AEAT.

Define un Protocol `PdfExtractor` para que el pipeline NO dependa del
backend de PDF concreto. Implementaciones:

- `PypdfExtractor`: backend por defecto, basado en la librería `pypdf`
  (pura Python, sin dependencias C, suficiente para texto plano de
  manuales AEAT que no llevan tablas complejas).
- `StubPdfExtractor`: backend para tests/fixtures. Lee un fichero `.txt`
  donde cada página está separada por `\\f` (form feed, carácter
  estándar para separar páginas en texto plano).

Si en el futuro hace falta extraer tablas o respetar columnas, se puede
añadir `PdfplumberExtractor` o `PymupdfExtractor` implementando el mismo
Protocol, sin tocar el chunker ni el runner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class PdfExtractorError(RuntimeError):
    """Error al extraer texto del PDF (corrupto, cifrado, dependencia ausente)."""


@dataclass(frozen=True)
class PageText:
    """Texto extraído de UNA página del PDF.

    `page_number` es 1-based (humano), no 0-based, para que el usuario
    pueda citar "Manual IRPF 2024, p. 215" verbatim.
    """

    page_number: int
    text: str


class PdfExtractor(Protocol):
    """Contrato mínimo: dado un path, devolver lista de páginas con texto."""

    def extract(self, pdf_path: Path) -> list[PageText]: ...


# ---------- StubPdfExtractor (tests / fixtures) ----------


class StubPdfExtractor:
    """Extractor que lee un fichero `.txt` con páginas separadas por `\\f`.

    Usado en tests/CI para evitar la dependencia de pypdf y permitir
    fixtures legibles directamente como texto. El operador puede
    también usarlo en producción si ya tiene los manuales convertidos
    a texto plano (por OCR previo, por ejemplo).
    """

    def extract(self, pdf_path: Path) -> list[PageText]:
        if not pdf_path.exists():
            raise PdfExtractorError(f"fichero no encontrado: {pdf_path}")
        try:
            raw = pdf_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise PdfExtractorError(
                f"no se pudo decodificar {pdf_path} como UTF-8: {exc}"
            ) from exc
        # Cada página separada por form feed (`\f`). Si no hay form feeds
        # consideramos que es una sola página.
        pages = raw.split("\f") if "\f" in raw else [raw]
        return [
            PageText(page_number=i + 1, text=page_text.strip())
            for i, page_text in enumerate(pages)
        ]


# ---------- PypdfExtractor (backend real) ----------


_RE_HYPHEN_LINEBREAK = re.compile(r"-\n(\w)")
_RE_MULTIPLE_NEWLINES = re.compile(r"\n{3,}")
_RE_TRAILING_SPACES = re.compile(r"[ \t]+$", re.MULTILINE)


def _normalize_pdf_text(raw: str) -> str:
    """Limpia el texto extraído de PDF.

    PDF tiene problemas comunes:
    - Palabras partidas a final de línea con guión (`re-\\nferida` → `referida`).
    - Múltiples saltos de línea por blocks vacíos.
    - Trailing spaces.

    No tocamos el contenido material — solo lo legible: las páginas
    permanecen separadas, los párrafos también; lo que se elimina es
    ruido tipográfico del extractor.
    """
    out = _RE_HYPHEN_LINEBREAK.sub(r"\1", raw)
    out = _RE_TRAILING_SPACES.sub("", out)
    out = _RE_MULTIPLE_NEWLINES.sub("\n\n", out)
    return out.strip()


class PypdfExtractor:
    """Extractor real basado en `pypdf` (pure Python, sin C deps).

    Importa `pypdf` perezosamente para que el resto del pipeline no
    cargue la dependencia hasta que se vaya a usar. Si `pypdf` no está
    instalado, lanzamos `PdfExtractorError` con un mensaje que indica
    cómo instalarlo (`pip install hacienda-ai[manuals]`).
    """

    def extract(self, pdf_path: Path) -> list[PageText]:
        try:
            import pypdf
        except ImportError as exc:
            raise PdfExtractorError(
                "pypdf no está instalado. Instala 'hacienda-ai[manuals]' "
                "para procesar PDFs reales, o usa StubPdfExtractor con un "
                "fichero .txt si ya tienes el manual en texto plano."
            ) from exc

        if not pdf_path.exists():
            raise PdfExtractorError(f"PDF no encontrado: {pdf_path}")

        try:
            reader = pypdf.PdfReader(str(pdf_path))
        except Exception as exc:  # pypdf lanza varias subclases.
            raise PdfExtractorError(
                f"no se pudo leer {pdf_path}: {exc}"
            ) from exc

        if reader.is_encrypted:
            # Intentamos desbloquear con contraseña vacía (común en PDFs
            # protegidos solo contra modificación).
            try:
                reader.decrypt("")
            except Exception as exc:
                raise PdfExtractorError(
                    f"{pdf_path}: PDF cifrado y no se pudo desbloquear: {exc}"
                ) from exc

        pages: list[PageText] = []
        for idx, page in enumerate(reader.pages):
            try:
                raw_text = page.extract_text() or ""
            except Exception as exc:
                # Una página problemática no debe abortar la ingesta del
                # manual entero — registramos texto vacío.
                raw_text = f"[error extrayendo página {idx + 1}: {exc}]"
            pages.append(
                PageText(
                    page_number=idx + 1,
                    text=_normalize_pdf_text(raw_text),
                )
            )
        return pages
