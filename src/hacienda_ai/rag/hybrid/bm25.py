"""BM25Okapi puro Python para retrieval léxico sparse.

BM25 es el sucesor de TF-IDF: rankea documentos por similitud léxica
con una query, penalizando términos muy frecuentes (IDF), normalizando
por longitud del documento, y saturando la frecuencia con un parámetro
`k1`. Es el baseline imbatible en retrieval léxico desde hace 20 años.

Esta implementación:

- Tokeniza con `re.findall(r"\\w+", text.lower())`. Sin stemming ni
  stopwords: para corpus legal español, las palabras "del", "la", "que"
  son menos discriminativas que el IDF ya las penaliza, y el stemming
  agresivo (`Spanish` stemmer de NLTK) introduce errores en términos
  jurídicos específicos. La pérdida de calidad es marginal y la
  ganancia en simplicidad/auditabilidad es alta.

- Construye un índice invertido en memoria. Para corpus medianos
  (≤100k chunks) cabe holgadamente. Si crece, se sustituye por
  Elasticsearch/Tantivy implementando el Protocol `SparseRetriever`.

- Parámetros estándar `k1=1.5`, `b=0.75` (valores del paper original
  BM25-3 de Robertson). Configurables.

API:

    retriever = BM25Retriever()
    retriever.index([(chunk_id_1, text_1), (chunk_id_2, text_2), ...])
    results = retriever.search("¿son deducibles los gastos de defensa?", top_k=10)
    # [(chunk_id, bm25_score), ...] ordenado descendente.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Protocol

_RE_TOKEN = re.compile(r"\w+", re.UNICODE)


def tokenize(text: str) -> list[str]:
    """Tokeniza a minúsculas con `\\w+`. Soporta acentos y Ñ."""
    return _RE_TOKEN.findall(text.lower())


class SparseRetriever(Protocol):
    """Contrato mínimo del retriever sparse.

    El indexado es idempotente sobre `chunk_id`: re-indexar el mismo
    id pisa el anterior.
    """

    def index(self, items: Iterable[tuple[str, str]]) -> int: ...

    def search(self, query: str, *, top_k: int = 10) -> list[tuple[str, float]]: ...

    def count(self) -> int: ...

    def delete(self, chunk_ids: list[str]) -> int: ...


@dataclass
class BM25Retriever:
    """BM25Okapi en memoria. Sin dependencias externas.

    `k1` controla la saturación de TF. `b` controla la normalización
    por longitud del documento. Defaults estándar del paper original.

    Implementación incremental: `index` puede llamarse varias veces
    para acumular más documentos. Las estadísticas (IDF, longitud media)
    se recalculan al hacer `search` — barato porque las mantenemos
    como cachés invalidadas.
    """

    k1: float = 1.5
    b: float = 0.75
    _docs: dict[str, list[str]] = field(default_factory=dict)
    _term_freq: dict[str, dict[str, int]] = field(default_factory=dict)
    _doc_lens: dict[str, int] = field(default_factory=dict)
    _idf_cache: dict[str, float] | None = field(default=None)
    _avg_dl_cache: float | None = field(default=None)

    # ---------- API pública ----------

    def index(self, items: Iterable[tuple[str, str]]) -> int:
        """Indexa pares `(chunk_id, text)`. Devuelve el número añadido/actualizado."""
        added = 0
        for chunk_id, text in items:
            tokens = tokenize(text)
            self._docs[chunk_id] = tokens
            self._term_freq[chunk_id] = dict(Counter(tokens))
            self._doc_lens[chunk_id] = len(tokens)
            added += 1
        self._invalidate_caches()
        return added

    def delete(self, chunk_ids: list[str]) -> int:
        deleted = 0
        for cid in chunk_ids:
            if cid in self._docs:
                del self._docs[cid]
                del self._term_freq[cid]
                del self._doc_lens[cid]
                deleted += 1
        if deleted:
            self._invalidate_caches()
        return deleted

    def count(self) -> int:
        return len(self._docs)

    def search(
        self, query: str, *, top_k: int = 10
    ) -> list[tuple[str, float]]:
        """Devuelve `[(chunk_id, score), ...]` ordenado por score desc.

        Para queries cortas (≤3 tokens) los scores son típicamente
        bajos; el caller debe combinar con el retrieval denso vía RRF
        antes de truncar.
        """
        if not self._docs:
            return []
        query_tokens = [t for t in tokenize(query) if t]
        if not query_tokens:
            return []
        idf = self._compute_idf()
        avg_dl = self._compute_avg_dl()

        scored: list[tuple[str, float]] = []
        for chunk_id, tf in self._term_freq.items():
            score = 0.0
            dl = self._doc_lens[chunk_id]
            for token in query_tokens:
                if token not in tf:
                    continue
                token_idf = idf.get(token, 0.0)
                freq = tf[token]
                numer = freq * (self.k1 + 1)
                denom = freq + self.k1 * (1 - self.b + self.b * dl / avg_dl)
                if denom > 0:
                    score += token_idf * numer / denom
            if score > 0:
                scored.append((chunk_id, score))
        # Orden estable: por score desc, desempate por chunk_id asc.
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[:top_k]

    # ---------- Internals ----------

    def _invalidate_caches(self) -> None:
        self._idf_cache = None
        self._avg_dl_cache = None

    def _compute_idf(self) -> dict[str, float]:
        """IDF de Robertson-Spärck Jones (BM25 variante con `+1`).

        Cachea el resultado hasta la próxima invalidación. Recalcular
        es O(corpus); con ~10k chunks tarda <50ms.
        """
        if self._idf_cache is not None:
            return self._idf_cache
        n = len(self._docs)
        if n == 0:
            self._idf_cache = {}
            return self._idf_cache
        df: dict[str, int] = {}
        for tf in self._term_freq.values():
            for token in tf:
                df[token] = df.get(token, 0) + 1
        idf: dict[str, float] = {}
        for token, doc_freq in df.items():
            # `+1` para evitar IDF negativo cuando un término aparece
            # en >50% de los docs (BM25 clásico permite negativo, pero
            # eso baja el score si el término aparece a menudo y aquí
            # preferimos un cero limpio).
            idf[token] = math.log(
                (n - doc_freq + 0.5) / (doc_freq + 0.5) + 1
            )
        self._idf_cache = idf
        return idf

    def _compute_avg_dl(self) -> float:
        if self._avg_dl_cache is not None:
            return self._avg_dl_cache
        if not self._doc_lens:
            self._avg_dl_cache = 0.0
            return 0.0
        avg = sum(self._doc_lens.values()) / len(self._doc_lens)
        self._avg_dl_cache = avg
        return avg
