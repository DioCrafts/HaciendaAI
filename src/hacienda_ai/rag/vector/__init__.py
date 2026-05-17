"""RAG vector store + embeddings para el corpus fiscal.

Patrón:

1. **Modelos del corpus** (`Norma`, `Sentencia`, `ConsultaDGT`,
   `ResolucionTEAC`, `ManualChunk`) → se mapean a `IndexableChunk` con
   metadata uniforme.
2. **`EmbeddingProvider`** Protocol embebe textos a vectores. Tres
   implementaciones:
   - `DeterministicHashEmbeddings`: hashing reproducible para tests/CI
     (sin red, sin API key).
   - `VoyageEmbeddings`: HTTP contra api.voyageai.com con `voyage-law-2`,
     modelo entrenado en corpus legal (mejor que embeddings genéricos
     para nuestro dominio fiscal/jurídico).
3. **`VectorStore`** Protocol persiste y busca. Dos implementaciones:
   - `InMemoryVectorStore`: cosine similarity en RAM, sin dependencias
     externas. Usada en tests/CI y en demos pequeñas.
   - `QdrantVectorStore`: HTTP REST contra Qdrant self-hosted
     (docker run -p 6333:6333 qdrant/qdrant). Producción.

El `runner.py` orquesta: carga corpus → embebe → upserta al store.
Filtros del query (`fecha_devengo`, `impuesto`, `source_types`) se
aplican antes de la búsqueda vectorial para que el retrieval respete
la vigencia normativa.
"""

from __future__ import annotations

from .corpus import (
    CorpusLoadError,
    iter_corpus_chunks,
    iter_dgt_chunks,
    iter_manual_chunks,
    iter_norma_chunks,
    iter_sentencia_chunks,
    iter_teac_chunks,
)
from .embedded_chunk import (
    EmbeddedChunk,
    IndexableChunk,
    SourceType,
    VectorMatch,
    VectorQuery,
)
from .memory import InMemoryVectorStore
from .provider import (
    DeterministicHashEmbeddings,
    EmbeddingProvider,
    EmbeddingProviderError,
)
from .qdrant import QdrantVectorStore
from .runner import (
    IndexReport,
    QueryReport,
    index_corpus,
    query_corpus,
)
from .store import VectorStore, VectorStoreError
from .voyage import VoyageEmbeddings

__all__ = [
    "CorpusLoadError",
    "DeterministicHashEmbeddings",
    "EmbeddedChunk",
    "EmbeddingProvider",
    "EmbeddingProviderError",
    "InMemoryVectorStore",
    "IndexReport",
    "IndexableChunk",
    "QdrantVectorStore",
    "QueryReport",
    "SourceType",
    "VectorMatch",
    "VectorQuery",
    "VectorStore",
    "VectorStoreError",
    "VoyageEmbeddings",
    "index_corpus",
    "iter_corpus_chunks",
    "iter_dgt_chunks",
    "iter_manual_chunks",
    "iter_norma_chunks",
    "iter_sentencia_chunks",
    "iter_teac_chunks",
    "query_corpus",
]
