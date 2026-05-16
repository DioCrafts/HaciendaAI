"""Retrieval híbrido: BM25 (sparse) + denso + re-ranker.

El retrieval puramente denso (embeddings) tiene dos fallos:

1. **Citas exactas**: "art. 19.2.e)" como término literal puede no
   embeber bien — los embeddings priorizan semántica sobre
   coincidencia léxica. BM25 sí premia la coincidencia exacta.
2. **Términos raros**: "ECLI:ES:TS:2024:1234" o "modelo 720"
   raramente aparecen en el corpus de entrenamiento del embedder.
   BM25 los rankea correctamente como tokens infrecuentes.

El re-ranker es la cereza del pastel: tras combinar BM25 + denso por
Reciprocal Rank Fusion, un cross-encoder (o Cohere Rerank API) reordena
los top-N candidatos puntuándolos contra la query con un modelo que SÍ
ha visto los dos a la vez. Esto típicamente mejora MRR/nDCG en un
10-20% sobre el ranking fusionado.

Componentes:

- **`BM25Retriever`**: BM25 puro Python (algoritmo BM25Okapi) sobre los
  textos de `IndexableChunk`. Sin dependencias externas. Vive en RAM
  (corpus mediano: ~100k chunks ⇒ <100 MB). Si el corpus crece, se
  sustituye por Elasticsearch/Tantivy implementando el Protocol
  `SparseRetriever`.

- **`Reranker`** Protocol con dos implementaciones:
  - `IdentityReranker`: orden por score original (no-op). Para CI/tests.
  - `CohereReranker`: HTTP contra api.cohere.com con
    `rerank-multilingual-v3.0`.

- **`HybridRetriever`**: combina los tres. Fusion por RRF (Reciprocal
  Rank Fusion), parametrizable `k_rrf` (60 por defecto, el valor del
  paper original). Devuelve `top_k` matches finales.

Diseño: se acopla a `VectorStore` (denso) e `EmbeddingProvider` por
inyección, igual que el resto del RAG. Para tests usamos
`InMemoryVectorStore` + `DeterministicHashEmbeddings` + `BM25Retriever`
local + `IdentityReranker`.
"""

from __future__ import annotations

from .bm25 import BM25Retriever, SparseRetriever
from .reranker import (
    CohereReranker,
    IdentityReranker,
    Reranker,
    RerankerError,
)
from .retrieval import (
    HybridRetriever,
    rrf_fuse,
)

__all__ = [
    "BM25Retriever",
    "CohereReranker",
    "HybridRetriever",
    "IdentityReranker",
    "Reranker",
    "RerankerError",
    "SparseRetriever",
    "rrf_fuse",
]
