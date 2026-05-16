"""Contrato del vector store y tipos comunes.

`VectorStore` Protocol con las operaciones mínimas:

- `ensure_collection(name, dim)`: idempotente, crea la colección con la
  dimensión correcta. Lanza si existe con otra dim (mezclar espacios
  vectoriales rompe el retrieval).
- `upsert(name, chunks)`: añade o actualiza chunks. El upsert es por
  `chunk_id`: re-indexar el mismo chunk pisa el anterior.
- `search(name, query_embedding, query)`: devuelve los top_k matches
  más similares aplicando los filtros del `VectorQuery`.
- `delete(name, chunk_ids)`: borra chunks. Lo usa la invalidación RAG
  cuando se detecta drift normativo.
- `count(name)`: número de chunks indexados, para auditoría.

El query del retrieval ya viene con su embedding pre-calculado: la
responsabilidad de embeber la query la tiene el caller (típicamente
`runner.query_corpus`). Esto evita acoplar `VectorStore` a un
`EmbeddingProvider` concreto.
"""

from __future__ import annotations

from typing import Protocol

from .embedded_chunk import EmbeddedChunk, VectorMatch, VectorQuery


class VectorStoreError(RuntimeError):
    """Error al operar contra el vector store."""


class VectorStore(Protocol):
    """Contrato mínimo de un backend vectorial."""

    def ensure_collection(self, name: str, *, dim: int) -> None: ...

    def upsert(self, name: str, chunks: list[EmbeddedChunk]) -> int: ...

    def search(
        self,
        name: str,
        *,
        query_embedding: tuple[float, ...],
        query: VectorQuery,
    ) -> list[VectorMatch]: ...

    def delete(self, name: str, chunk_ids: list[str]) -> int: ...

    def count(self, name: str) -> int: ...
