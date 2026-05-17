"""Vector store en memoria. Sin dependencias externas.

Implementación lineal de búsqueda por cosine similarity. No es
eficiente para corpus grandes (>~100k chunks), pero:

- Es suficiente para tests/CI.
- Es suficiente para demos pequeñas con cientos o miles de chunks.
- Sirve como referencia: el comportamiento de `QdrantVectorStore` con
  vectores normalizados y `Cosine` distance debe ser idéntico, salvo
  por orden cuando hay empates de score.

Los filtros de `VectorQuery` (source_type, impuesto, fecha_devengo)
se aplican antes de calcular similitudes — para corpus grandes esto
acelera la búsqueda significativamente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .embedded_chunk import (
    EmbeddedChunk,
    VectorMatch,
    VectorQuery,
)
from .store import VectorStoreError


@dataclass
class _Collection:
    dim: int
    chunks: dict[str, EmbeddedChunk] = field(default_factory=dict)


class InMemoryVectorStore:
    """Vector store en RAM. Cosine similarity sobre vectores normalizados."""

    def __init__(self) -> None:
        self._collections: dict[str, _Collection] = {}

    # ---------- API VectorStore ----------

    def ensure_collection(self, name: str, *, dim: int) -> None:
        if name in self._collections:
            existing = self._collections[name]
            if existing.dim != dim:
                raise VectorStoreError(
                    f"colección {name!r} ya existe con dim={existing.dim}, "
                    f"se pidió dim={dim}. Borra la colección o usa el dim correcto."
                )
            return
        if dim <= 0:
            raise VectorStoreError(f"dim inválida: {dim}")
        self._collections[name] = _Collection(dim=dim)

    def upsert(self, name: str, chunks: list[EmbeddedChunk]) -> int:
        col = self._require(name)
        upserted = 0
        for chunk in chunks:
            if chunk.vector_dim != col.dim:
                raise VectorStoreError(
                    f"chunk {chunk.chunk_id}: dim={chunk.vector_dim}, "
                    f"esperada {col.dim}"
                )
            col.chunks[chunk.chunk_id] = chunk
            upserted += 1
        return upserted

    def delete(self, name: str, chunk_ids: list[str]) -> int:
        col = self._require(name)
        deleted = 0
        for cid in chunk_ids:
            if cid in col.chunks:
                del col.chunks[cid]
                deleted += 1
        return deleted

    def count(self, name: str) -> int:
        return len(self._require(name).chunks)

    def search(
        self,
        name: str,
        *,
        query_embedding: tuple[float, ...],
        query: VectorQuery,
    ) -> list[VectorMatch]:
        col = self._require(name)
        if len(query_embedding) != col.dim:
            raise VectorStoreError(
                f"query_embedding dim={len(query_embedding)}, colección dim={col.dim}"
            )

        candidates = [
            chunk
            for chunk in col.chunks.values()
            if _passes_filters(chunk, query)
        ]
        scored = [
            VectorMatch(
                chunk=chunk,
                score=_cosine_similarity(query_embedding, chunk.embedding),
            )
            for chunk in candidates
        ]
        # Orden descendente por score, desempate por chunk_id para
        # determinismo en tests.
        scored.sort(key=lambda m: (-m.score, m.chunk.chunk_id))
        filtered = [m for m in scored if m.score >= query.min_score]
        return filtered[: query.top_k]

    # ---------- Helpers ----------

    def _require(self, name: str) -> _Collection:
        col = self._collections.get(name)
        if col is None:
            raise VectorStoreError(f"colección {name!r} no existe")
        return col


def _cosine_similarity(
    a: tuple[float, ...], b: tuple[float, ...]
) -> float:
    """Cosine similarity. Asume vectores no nulos.

    Para vectores ya normalizados (caso típico de Voyage tras la
    documentación de Voyage AI) esto equivale a dot product.
    """
    if len(a) != len(b):
        raise ValueError(f"dim mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    denom = norm_a * norm_b
    if denom == 0:
        return 0.0
    return float(dot / denom)


def _passes_filters(chunk: EmbeddedChunk, query: VectorQuery) -> bool:
    """Aplica los filtros declarados en el query al metadata del chunk."""
    if query.source_types is not None:
        if chunk.source_type not in query.source_types:
            return False
    if query.impuesto is not None:
        if chunk.metadata.get("impuesto") != query.impuesto:
            return False
    if query.fecha_devengo is not None:
        if not _covers_fecha_devengo(chunk.metadata, query.fecha_devengo):
            return False
    return True


def _covers_fecha_devengo(metadata: dict[str, Any], target: date) -> bool:
    """Filtro temporal: chunk vigente en `target`.

    Reglas:
    - Si no hay `effective_from` ni `effective_to`, el chunk se considera
      atemporal (manuales sin fecha, FAQs INFORMA…). Lo aceptamos: el
      caller que necesite estricto debe pre-filtrar.
    - Si hay `effective_from`, target debe ser >= esa fecha.
    - Si hay `effective_to` y no es null, target debe ser <= esa fecha.
    """
    eff_from = _parse_date(metadata.get("effective_from"))
    eff_to = _parse_date(metadata.get("effective_to"))
    if eff_from is not None and target < eff_from:
        return False
    if eff_to is not None and target > eff_to:
        return False
    return True


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None
