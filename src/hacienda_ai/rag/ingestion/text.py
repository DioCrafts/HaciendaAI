"""Extracción de texto plano de los documentos descargados.

Soporta HTML (BeautifulSoup) y texto plano. PDF está intencionalmente
fuera del MVP: el catálogo prioriza versiones HTML consolidadas del
BOE. Si añades una fuente PDF, integra una librería como pypdf en un
PR aparte.
"""

from __future__ import annotations

from pathlib import Path

from bs4 import BeautifulSoup


def extract_text(path: Path) -> str:
    """Devuelve el texto plano del documento cacheado."""
    suffix = path.suffix.lower()
    raw = path.read_bytes()
    if suffix in {".html", ".htm"}:
        return _html_to_text(raw)
    if suffix == ".txt":
        return raw.decode("utf-8", errors="replace")
    if suffix == ".pdf":
        return (
            f"[PDF no soportado en MVP: {path.name}. "
            "Integra una librería de parseo de PDF antes de indexar este documento.]"
        )
    return raw.decode("utf-8", errors="replace")


def _html_to_text(raw: bytes) -> str:
    soup = BeautifulSoup(raw, "html.parser")
    # Eliminar nodos sin contenido legal útil
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Normaliza espacios pero conserva saltos de línea como separadores
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)
