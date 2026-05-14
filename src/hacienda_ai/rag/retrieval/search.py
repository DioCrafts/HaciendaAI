"""Búsqueda por palabra clave sobre las fuentes cacheadas.

Implementación deliberadamente simple para el MVP:
- Tokeniza la query en términos no vacíos.
- Para cada fuente cacheada, extrae el texto y cuenta ocurrencias
  case-insensitive de cada término.
- Ordena por suma de ocurrencias y devuelve los primeros N con
  snippets de contexto (~120 chars alrededor de la primera coincidencia).

No es un BM25 propiamente: las fuentes son pocas (~15) y los textos
no llegan a varias MB. Si el corpus crece, sustituir por whoosh,
rank_bm25 o un servicio externo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ..ingestion.fetcher import DEFAULT_CACHE_DIR, cached_path
from ..ingestion.text import extract_text
from ..sources.catalog import CATALOG, OfficialSource

SNIPPET_RADIUS = 120


@dataclass(frozen=True)
class SearchHit:
    source: OfficialSource
    score: int
    snippet: str


def _tokenize(query: str) -> list[str]:
    return [token.lower() for token in re.findall(r"\w+", query) if len(token) > 1]


def _snippet(text: str, term: str) -> str:
    lower = text.lower()
    index = lower.find(term)
    if index == -1:
        return ""
    start = max(0, index - SNIPPET_RADIUS)
    end = min(len(text), index + len(term) + SNIPPET_RADIUS)
    excerpt = text[start:end]
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{excerpt}{suffix}".replace("\n", " ")


def search(
    query: str,
    *,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    top_k: int = 5,
    sources: tuple[OfficialSource, ...] = CATALOG,
) -> list[SearchHit]:
    """Busca `query` en todas las fuentes cacheadas. Las fuentes no
    descargadas se omiten silenciosamente."""
    terms = _tokenize(query)
    if not terms:
        return []
    hits: list[SearchHit] = []
    for source in sources:
        path = cached_path(cache_dir, source)
        if not path.exists():
            continue
        try:
            text = extract_text(path)
        except OSError:
            continue
        if not text:
            continue
        lower_text = text.lower()
        score = sum(lower_text.count(term) for term in terms)
        if score == 0:
            continue
        snippet = next(
            (snippet for snippet in (_snippet(text, term) for term in terms) if snippet),
            "",
        )
        hits.append(SearchHit(source=source, score=score, snippet=snippet))
    hits.sort(key=lambda hit: (-hit.score, hit.source.id))
    return hits[:top_k]
