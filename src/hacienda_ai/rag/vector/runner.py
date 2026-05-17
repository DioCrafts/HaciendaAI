"""Pipeline de indexación y consulta del corpus en el vector store.

`index_corpus`: lee chunks del corpus, embebe, upserta al store.
`query_corpus`: embebe la consulta, busca en el store, devuelve matches.

Ambos son agnósticos del proveedor y del store: reciben los objetos
inyectados. En CI/tests usamos `DeterministicHashEmbeddings` +
`InMemoryVectorStore`; en producción `VoyageEmbeddings` +
`QdrantVectorStore`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .embedded_chunk import (
    EmbeddedChunk,
    IndexableChunk,
    VectorMatch,
    VectorQuery,
)
from .provider import EmbeddingProvider
from .store import VectorStore


@dataclass
class IndexReport:
    """Resultado de una indexación."""

    collection: str
    total_chunks: int = 0
    upserted: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class QueryReport:
    """Resultado de una consulta semántica."""

    query: VectorQuery
    matches: list[VectorMatch] = field(default_factory=list)


def index_corpus(
    chunks: Iterable[IndexableChunk],
    *,
    collection: str,
    provider: EmbeddingProvider,
    store: VectorStore,
    batch_size: int = 32,
) -> IndexReport:
    """Embebe y upserta chunks al vector store.

    Procesamiento en batches para amortizar el coste de la API de
    embeddings y reducir el número de upserts.

    Garantiza que la colección existe con la dimensión correcta antes
    de empezar. Si la colección ya existe con otra dimensión, el store
    lanza — para evitar mezclar espacios vectoriales.
    """
    report = IndexReport(collection=collection)
    store.ensure_collection(collection, dim=provider.dim)

    buffer: list[IndexableChunk] = []
    for chunk in chunks:
        buffer.append(chunk)
        report.total_chunks += 1
        if len(buffer) >= batch_size:
            _flush(buffer, collection=collection, provider=provider, store=store, report=report)
            buffer = []
    if buffer:
        _flush(buffer, collection=collection, provider=provider, store=store, report=report)
    return report


def _flush(
    buffer: list[IndexableChunk],
    *,
    collection: str,
    provider: EmbeddingProvider,
    store: VectorStore,
    report: IndexReport,
) -> None:
    """Embebe el buffer y lo upserta. Acumula errores sin abortar."""
    texts = [c.text for c in buffer]
    try:
        vectors = provider.embed_documents(texts)
    except Exception as exc:  # noqa: BLE001
        # Si el batch falla, registramos el error de los chunks afectados
        # pero seguimos con los siguientes (un PDF mal extraído no debe
        # tirar la indexación entera).
        report.errors.append(
            f"embed batch ({len(buffer)} chunks): {exc}"
        )
        return
    if len(vectors) != len(buffer):
        report.errors.append(
            f"embed batch devolvió {len(vectors)} vectores para {len(buffer)} chunks"
        )
        return
    embedded = [
        EmbeddedChunk(
            chunk_id=c.chunk_id,
            source_type=c.source_type,
            text=c.text,
            embedding=v,
            embedding_model=provider.model_id,
            metadata=c.metadata,
        )
        for c, v in zip(buffer, vectors, strict=True)
    ]
    try:
        upserted = store.upsert(collection, embedded)
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"upsert batch ({len(buffer)} chunks): {exc}")
        return
    report.upserted += upserted


def query_corpus(
    query: VectorQuery,
    *,
    collection: str,
    provider: EmbeddingProvider,
    store: VectorStore,
) -> QueryReport:
    """Embebe la consulta y busca en el store. Aplica filtros del query.

    Llama a `embed_query` (no `embed_documents`) para que el proveedor
    aplique la instrucción asimétrica cuando exista (Voyage usa
    `input_type=query` aquí).
    """
    query_vec = provider.embed_query(query.text)
    matches = store.search(
        collection, query_embedding=query_vec, query=query
    )
    return QueryReport(query=query, matches=matches)
