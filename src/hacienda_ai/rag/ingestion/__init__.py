"""Descarga, caché y extracción de texto de las fuentes oficiales."""

from .fetcher import DEFAULT_CACHE_DIR, FetchResult, cache_status, fetch_all, fetch_source
from .text import extract_text

__all__ = [
    "DEFAULT_CACHE_DIR",
    "FetchResult",
    "cache_status",
    "extract_text",
    "fetch_all",
    "fetch_source",
]
