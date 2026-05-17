"""Test de integración del runner: corpus → embed → upsert + query."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from hacienda_ai.rag.vector import (
    DeterministicHashEmbeddings,
    InMemoryVectorStore,
    SourceType,
    VectorQuery,
    index_corpus,
    iter_corpus_chunks,
    iter_dgt_chunks,
    iter_manual_chunks,
    iter_sentencia_chunks,
    query_corpus,
)

CORPUS = Path(__file__).parent / "fixtures" / "vector" / "corpus"


def _setup() -> tuple[
    DeterministicHashEmbeddings, InMemoryVectorStore, str
]:
    provider = DeterministicHashEmbeddings(dim=512)
    store = InMemoryVectorStore()
    return provider, store, "test_collection"


def test_index_corpus_completo() -> None:
    provider, store, col = _setup()
    chunks = list(iter_corpus_chunks(CORPUS))
    report = index_corpus(
        chunks, collection=col, provider=provider, store=store
    )
    assert report.total_chunks == 6
    assert report.upserted == 6
    assert report.errors == []
    assert store.count(col) == 6


def test_index_corpus_ensure_collection_dim_matches_provider() -> None:
    provider, store, col = _setup()
    chunks = list(iter_corpus_chunks(CORPUS))
    index_corpus(chunks, collection=col, provider=provider, store=store)
    # La colección se creó con la dim del provider.
    assert provider.dim == 512


def test_index_es_idempotente_por_chunk_id() -> None:
    provider, store, col = _setup()
    chunks = list(iter_corpus_chunks(CORPUS))
    index_corpus(chunks, collection=col, provider=provider, store=store)
    index_corpus(chunks, collection=col, provider=provider, store=store)
    # Re-indexar el mismo corpus NO duplica.
    assert store.count(col) == 6


def test_index_acumula_errores_sin_abortar() -> None:
    """Si el provider falla en un batch, el resto sigue procesándose."""
    from hacienda_ai.rag.vector import EmbeddingProviderError

    class FlakyProvider:
        model_id = "flaky"
        dim = 8

        def __init__(self) -> None:
            self.calls = 0

        def embed_documents(self, texts: list[str]) -> list[tuple[float, ...]]:
            self.calls += 1
            if self.calls == 1:
                # Primer batch falla.
                raise EmbeddingProviderError("simulated outage")
            return [(1.0,) * 8 for _ in texts]

        def embed_query(self, text: str) -> tuple[float, ...]:
            return (1.0,) * 8

    provider = FlakyProvider()
    store = InMemoryVectorStore()
    chunks = list(iter_corpus_chunks(CORPUS))
    report = index_corpus(
        chunks,
        collection="c",
        provider=provider,  # type: ignore[arg-type]
        store=store,
        batch_size=3,
    )
    # Primer batch (3 chunks) erra; segundo batch (3 chunks) pasa.
    assert len(report.errors) == 1
    assert report.upserted == 3
    assert "simulated outage" in report.errors[0]


def test_query_corpus_recupera_chunk_relevante() -> None:
    """Búsqueda por keyword fiscal recupera el chunk que la contiene.

    Con `DeterministicHashEmbeddings` la similitud es básicamente
    bolsa-de-palabras. Suficiente para verificar que el pipeline
    funciona end-to-end.
    """
    provider, store, col = _setup()
    chunks = list(iter_corpus_chunks(CORPUS))
    index_corpus(chunks, collection=col, provider=provider, store=store)

    query = VectorQuery(text="rendimientos del trabajo", top_k=3)
    report = query_corpus(
        query, collection=col, provider=provider, store=store
    )
    assert len(report.matches) >= 1
    # Al menos uno de los matches debe ser el chunk del manual IRPF
    # (que habla específicamente de rendimientos del trabajo).
    top_ids = [m.chunk.chunk_id for m in report.matches]
    assert any("manual" in cid for cid in top_ids)


def test_query_corpus_aplica_filtro_source_type() -> None:
    provider, store, col = _setup()
    chunks = list(iter_corpus_chunks(CORPUS))
    index_corpus(chunks, collection=col, provider=provider, store=store)

    query = VectorQuery(
        text="rendimientos del trabajo",
        top_k=10,
        source_types=(SourceType.SENTENCIA,),
    )
    report = query_corpus(
        query, collection=col, provider=provider, store=store
    )
    assert all(
        m.chunk.source_type == SourceType.SENTENCIA
        for m in report.matches
    )


def test_query_corpus_aplica_filtro_impuesto() -> None:
    provider, store, col = _setup()
    chunks = list(iter_corpus_chunks(CORPUS))
    index_corpus(chunks, collection=col, provider=provider, store=store)

    query = VectorQuery(
        text="cuestión fiscal",
        top_k=10,
        impuesto="irpf",
    )
    report = query_corpus(
        query, collection=col, provider=provider, store=store
    )
    for m in report.matches:
        # Solo recupera chunks cuyo metadata.impuesto == "irpf".
        assert m.chunk.metadata.get("impuesto") == "irpf"


def test_query_corpus_filtro_temporal_excluye_normas_no_vigentes() -> None:
    provider, store, col = _setup()
    chunks = list(iter_corpus_chunks(CORPUS))
    index_corpus(chunks, collection=col, provider=provider, store=store)

    # Fecha anterior a la entrada en vigor de la LIRPF (2007-01-01).
    # La norma del fixture vigente desde 2007 debe excluirse.
    query = VectorQuery(
        text="LIRPF",
        top_k=10,
        source_types=(SourceType.NORMA,),
        fecha_devengo=date(2005, 1, 1),
    )
    report = query_corpus(
        query, collection=col, provider=provider, store=store
    )
    assert report.matches == []


def test_index_solo_una_fuente() -> None:
    """`iter_<fuente>_chunks` permite indexar selectivamente."""
    provider, store, col = _setup()
    sentencias = list(iter_sentencia_chunks(CORPUS / "jurisprudencia"))
    index_corpus(
        sentencias, collection=col, provider=provider, store=store
    )
    assert store.count(col) == 1


def test_index_dgt_y_manuales_en_misma_coleccion() -> None:
    """Coleccionar fuentes mezcladas funciona — el `source_type` queda
    en la metadata y permite filtrar al consultar."""
    provider, store, col = _setup()
    mixed = list(iter_dgt_chunks(CORPUS / "dgt_consultas")) + list(
        iter_manual_chunks(CORPUS / "manuales")
    )
    index_corpus(mixed, collection=col, provider=provider, store=store)
    # 1 DGT + 2 manuales (manual_irpf + INFORMA) = 3.
    assert store.count(col) == 3
