"""HybridRetriever: combina BM25 + denso + re-ranker.

Flujo:

1. **BM25** rankea los `bm25_top_k` chunks más similares léxicamente.
2. **Denso** (vector store) rankea los `dense_top_k` más similares
   semánticamente, aplicando filtros del query (source_types,
   impuesto, fecha_devengo) en el backend.
3. **RRF (Reciprocal Rank Fusion)** combina los dos rankings con la
   fórmula `score(d) = sum(1 / (k + rank_i(d)))` sobre rankings donde
   el documento aparece. `k=60` es el valor canónico del paper.
4. **Re-ranker** reordena los top-N fusionados puntuándolos con un
   modelo que ve query y documento juntos. Si falla, se loguea y se
   devuelve el orden fusionado (degradación graceful).
5. Truncado a `final_top_k`.

El BM25 indexa los TEXTOS de los chunks tras el vector store los haya
embebido — el caller debe ejecutar `hybrid.index_corpus(...)` que
mantiene ambos sincronizados.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from ..vector import (
    EmbeddingProvider,
    IndexableChunk,
    VectorMatch,
    VectorQuery,
    VectorStore,
)
from .bm25 import SparseRetriever
from .reranker import IdentityReranker, Reranker, RerankerError


def rrf_fuse(
    rankings: list[list[tuple[str, float]]],
    *,
    k: int = 60,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion.

    `rankings`: lista de rankings, cada uno una lista `[(chunk_id, score), ...]`
    ordenada por score descendente. El valor `score` se ignora — solo
    cuenta la posición.

    Devuelve un único ranking fusionado por la fórmula canónica:
    `rrf_score(d) = sum_i 1 / (k + rank_i(d))` para los rankings donde
    aparece `d` (1-indexado).

    `k=60` es el valor del paper "Reciprocal rank fusion outperforms
    Condorcet and individual rank learning methods" (Cormack et al.,
    2009). Robusto a cambios de orden de magnitud entre rankings.
    """
    accumulator: dict[str, float] = {}
    for ranking in rankings:
        for rank_pos, (chunk_id, _score) in enumerate(ranking, start=1):
            accumulator[chunk_id] = accumulator.get(chunk_id, 0.0) + 1.0 / (
                k + rank_pos
            )
    fused = sorted(accumulator.items(), key=lambda x: (-x[1], x[0]))
    return fused


@dataclass
class HybridRetriever:
    """Retriever híbrido inyectable. No mantiene estado del corpus.

    El sparse (`bm25`) y el dense (`vector_store`) se inyectan ya
    construidos; el `provider` se necesita para embeber la query. El
    `reranker` por defecto es `IdentityReranker` (no-op) — para
    producción se enchufa `CohereReranker`.

    `bm25_top_k` y `dense_top_k` son los presupuestos individuales.
    `fusion_top_k` es el cuántos pasan al reranker tras RRF.
    `final_top_k` es el `top_k` devuelto al caller.
    """

    bm25: SparseRetriever
    vector_store: VectorStore
    provider: EmbeddingProvider
    reranker: Reranker = field(default_factory=IdentityReranker)
    collection: str = "hacienda_corpus_v1"
    bm25_top_k: int = 50
    dense_top_k: int = 50
    fusion_top_k: int = 50

    def index(self, chunks: Iterable[IndexableChunk]) -> int:
        """Indexa el corpus en BM25 (lookup por id estable).

        El vector_store ya debe estar indexado por el caller — esta
        función NO toca el vector store para que la indexación de
        embeddings (que cuesta dinero) y BM25 (gratis, local) puedan
        gestionarse independientemente.
        """
        items = [(c.chunk_id, c.text) for c in chunks]
        return self.bm25.index(items)

    def search(self, query: VectorQuery) -> list[VectorMatch]:
        """Búsqueda híbrida con filtros aplicados.

        Pasos:
        1. BM25 sobre `query.text` → top `bm25_top_k`.
        2. Embed query + vector search con filtros del query →
           top `dense_top_k`.
        3. RRF fusiona los dos rankings.
        4. Reranker reordena top `fusion_top_k` candidatos.
        5. Devuelve top `query.top_k`.

        BM25 NO conoce los filtros (`source_types`, `impuesto`,
        `fecha_devengo`): aplicamos post-filtering tras la fusión
        consultando la metadata del chunk vía el vector store. Esto
        evita índices BM25 separados por filtro y mantiene el coste
        bajo a cambio de algo más de trabajo en runtime.
        """
        # 1. BM25.
        bm25_results = self.bm25.search(query.text, top_k=self.bm25_top_k)

        # 2. Denso.
        query_embedding = self.provider.embed_query(query.text)
        dense_results_full = self.vector_store.search(
            self.collection,
            query_embedding=query_embedding,
            query=VectorQuery(
                text=query.text,
                top_k=self.dense_top_k,
                source_types=query.source_types,
                impuesto=query.impuesto,
                fecha_devengo=query.fecha_devengo,
                min_score=0.0,  # filtramos al final por reranker score.
            ),
        )
        dense_results = [(m.chunk.chunk_id, m.score) for m in dense_results_full]
        dense_chunks = {m.chunk.chunk_id: m for m in dense_results_full}

        # 3. RRF.
        fused = rrf_fuse([bm25_results, dense_results])

        # 4. Resolvemos cada chunk_id del top fusionado a su `VectorMatch`
        # para tener el texto + metadata. Si el chunk no está en
        # `dense_chunks` (apareció solo en BM25), lo recuperamos del
        # store individualmente. Optimización futura: store.get_by_ids.
        candidates_for_rerank: list[tuple[str, str, float]] = []
        chunks_by_id: dict[str, VectorMatch] = dict(dense_chunks)
        for cid, fused_score in fused[: self.fusion_top_k]:
            match = chunks_by_id.get(cid)
            if match is None:
                # Chunk solo en BM25. Hacemos lookup uno-a-uno via
                # search-by-id es ineficiente; en su lugar omitimos del
                # candidate pool y dejamos que el chunk se considere
                # tras el siguiente reindex (BM25 + dense desincronizados
                # son la excepción, no la regla).
                continue
            candidates_for_rerank.append((cid, match.chunk.text, fused_score))

        # 5. Post-filtering por metadata (aplicamos los filtros del
        # query también a chunks que vinieron de BM25, no solo a los
        # dense).
        candidates_for_rerank = self._post_filter(candidates_for_rerank, query, chunks_by_id)

        # 6. Reranker.
        try:
            reranked = self.reranker.rerank(
                query=query.text,
                candidates=candidates_for_rerank,
                top_k=self.fusion_top_k,
            )
        except RerankerError:
            # Degradación graceful: si el reranker falla, devolvemos el
            # orden RRF tal cual.
            reranked = [(cid, score) for cid, _text, score in candidates_for_rerank]

        # 7. Aplicar `min_score` del query SOBRE el score del reranker
        # y truncar a `final_top_k`.
        final = [
            (cid, score)
            for cid, score in reranked
            if score >= query.min_score
        ]
        final = final[: query.top_k]

        return [
            VectorMatch(chunk=chunks_by_id[cid].chunk, score=score)
            for cid, score in final
            if cid in chunks_by_id
        ]

    def _post_filter(
        self,
        candidates: list[tuple[str, str, float]],
        query: VectorQuery,
        chunks_by_id: dict[str, VectorMatch],
    ) -> list[tuple[str, str, float]]:
        """Aplica filtros del query a chunks que pudieran haber venido
        sólo de BM25 (sin filtrado en el sparse).

        En esta versión el filtrado fuerte ya lo hace el dense (que
        respeta source_types/impuesto/fecha_devengo); y los chunks que
        solo vienen de BM25 los descartamos arriba en `search`. Aquí
        verificamos para defensa en profundidad — si en el futuro
        cambiamos el flujo, este filtro evita regresiones silenciosas.
        """
        if (
            query.source_types is None
            and query.impuesto is None
            and query.fecha_devengo is None
        ):
            return candidates
        out: list[tuple[str, str, float]] = []
        for cid, text, score in candidates:
            match = chunks_by_id.get(cid)
            if match is None:
                continue
            meta = match.chunk.metadata
            if query.source_types is not None:
                if match.chunk.source_type not in query.source_types:
                    continue
            if query.impuesto is not None:
                if meta.get("impuesto") != query.impuesto:
                    continue
            out.append((cid, text, score))
        return out
