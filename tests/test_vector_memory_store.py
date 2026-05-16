"""Tests del vector store en memoria."""

from __future__ import annotations

from datetime import date

import pytest

from hacienda_ai.rag.vector import (
    EmbeddedChunk,
    InMemoryVectorStore,
    SourceType,
    VectorMatch,
    VectorQuery,
    VectorStoreError,
)


def _chunk(
    chunk_id: str,
    embedding: tuple[float, ...],
    *,
    source_type: SourceType = SourceType.NORMA,
    metadata: dict | None = None,
) -> EmbeddedChunk:
    return EmbeddedChunk(
        chunk_id=chunk_id,
        source_type=source_type,
        text=f"texto de {chunk_id}",
        embedding=embedding,
        embedding_model="test",
        metadata=metadata or {},
    )


def test_ensure_collection_es_idempotente() -> None:
    store = InMemoryVectorStore()
    store.ensure_collection("c", dim=4)
    store.ensure_collection("c", dim=4)  # no debe lanzar.
    assert store.count("c") == 0


def test_ensure_collection_dim_mismatch_lanza() -> None:
    store = InMemoryVectorStore()
    store.ensure_collection("c", dim=4)
    with pytest.raises(VectorStoreError):
        store.ensure_collection("c", dim=8)


def test_upsert_y_count() -> None:
    store = InMemoryVectorStore()
    store.ensure_collection("c", dim=2)
    upserted = store.upsert(
        "c", [_chunk("a", (1.0, 0.0)), _chunk("b", (0.0, 1.0))]
    )
    assert upserted == 2
    assert store.count("c") == 2


def test_upsert_dim_mismatch_lanza() -> None:
    store = InMemoryVectorStore()
    store.ensure_collection("c", dim=4)
    with pytest.raises(VectorStoreError):
        store.upsert("c", [_chunk("a", (1.0, 0.0))])


def test_upsert_es_idempotente_por_chunk_id() -> None:
    """Re-upsertar el mismo id pisa el anterior, no crea duplicado."""
    store = InMemoryVectorStore()
    store.ensure_collection("c", dim=2)
    store.upsert("c", [_chunk("a", (1.0, 0.0))])
    store.upsert("c", [_chunk("a", (0.5, 0.5))])
    assert store.count("c") == 1


def test_delete_quita_chunks_existentes() -> None:
    store = InMemoryVectorStore()
    store.ensure_collection("c", dim=2)
    store.upsert("c", [_chunk("a", (1.0, 0.0)), _chunk("b", (0.0, 1.0))])
    deleted = store.delete("c", ["a", "no-existe"])
    assert deleted == 1
    assert store.count("c") == 1


def test_search_devuelve_top_k_ordenado() -> None:
    store = InMemoryVectorStore()
    store.ensure_collection("c", dim=3)
    store.upsert(
        "c",
        [
            _chunk("a", (1.0, 0.0, 0.0)),  # cosine 1.0 con query (1,0,0)
            _chunk("b", (0.6, 0.8, 0.0)),  # cosine 0.6
            _chunk("c", (0.0, 1.0, 0.0)),  # cosine 0.0
        ],
    )
    matches = store.search(
        "c",
        query_embedding=(1.0, 0.0, 0.0),
        query=VectorQuery(text="dummy", top_k=2),
    )
    assert len(matches) == 2
    assert matches[0].chunk.chunk_id == "a"
    assert matches[0].score == 1.0
    assert matches[1].chunk.chunk_id == "b"
    assert 0.55 < matches[1].score < 0.65


def test_search_filtra_por_source_types() -> None:
    store = InMemoryVectorStore()
    store.ensure_collection("c", dim=2)
    store.upsert(
        "c",
        [
            _chunk("n1", (1.0, 0.0), source_type=SourceType.NORMA),
            _chunk("s1", (1.0, 0.0), source_type=SourceType.SENTENCIA),
        ],
    )
    matches = store.search(
        "c",
        query_embedding=(1.0, 0.0),
        query=VectorQuery(
            text="dummy",
            top_k=10,
            source_types=(SourceType.SENTENCIA,),
        ),
    )
    assert len(matches) == 1
    assert matches[0].chunk.chunk_id == "s1"


def test_search_filtra_por_impuesto() -> None:
    store = InMemoryVectorStore()
    store.ensure_collection("c", dim=2)
    store.upsert(
        "c",
        [
            _chunk("a", (1.0, 0.0), metadata={"impuesto": "irpf"}),
            _chunk("b", (1.0, 0.0), metadata={"impuesto": "iva"}),
        ],
    )
    matches = store.search(
        "c",
        query_embedding=(1.0, 0.0),
        query=VectorQuery(text="dummy", impuesto="irpf"),
    )
    assert len(matches) == 1
    assert matches[0].chunk.chunk_id == "a"


def test_search_filtra_por_fecha_devengo() -> None:
    """Solo chunks vigentes en la fecha pasan el filtro temporal."""
    store = InMemoryVectorStore()
    store.ensure_collection("c", dim=2)
    store.upsert(
        "c",
        [
            _chunk(
                "vigente",
                (1.0, 0.0),
                metadata={
                    "effective_from": "2007-01-01",
                    "effective_to": "2025-12-31",
                },
            ),
            _chunk(
                "derogada",
                (1.0, 0.0),
                metadata={
                    "effective_from": "2000-01-01",
                    "effective_to": "2006-12-31",
                },
            ),
            _chunk(
                "futura",
                (1.0, 0.0),
                metadata={"effective_from": "2030-01-01"},
            ),
            _chunk(
                "atemporal",
                (1.0, 0.0),
                metadata={},  # sin fechas: pasa siempre.
            ),
        ],
    )
    matches = store.search(
        "c",
        query_embedding=(1.0, 0.0),
        query=VectorQuery(text="dummy", fecha_devengo=date(2024, 1, 1)),
    )
    ids = {m.chunk.chunk_id for m in matches}
    assert ids == {"vigente", "atemporal"}


def test_search_min_score_descarta_resultados_pobres() -> None:
    store = InMemoryVectorStore()
    store.ensure_collection("c", dim=2)
    store.upsert(
        "c",
        [
            _chunk("alta", (1.0, 0.0)),
            _chunk("baja", (0.0, 1.0)),  # cosine 0 con (1,0).
        ],
    )
    matches = store.search(
        "c",
        query_embedding=(1.0, 0.0),
        query=VectorQuery(text="dummy", min_score=0.5),
    )
    assert len(matches) == 1
    assert matches[0].chunk.chunk_id == "alta"


def test_search_dim_mismatch_lanza() -> None:
    store = InMemoryVectorStore()
    store.ensure_collection("c", dim=4)
    with pytest.raises(VectorStoreError):
        store.search(
            "c",
            query_embedding=(1.0, 0.0),
            query=VectorQuery(text="dummy"),
        )


def test_search_orden_determinista_en_empates() -> None:
    """Empates de score se desempatan por chunk_id (no por orden de inserción)."""
    store = InMemoryVectorStore()
    store.ensure_collection("c", dim=2)
    store.upsert(
        "c",
        [
            _chunk("z", (1.0, 0.0)),
            _chunk("a", (1.0, 0.0)),
            _chunk("m", (1.0, 0.0)),
        ],
    )
    matches = store.search(
        "c",
        query_embedding=(1.0, 0.0),
        query=VectorQuery(text="dummy", top_k=3),
    )
    ids = [m.chunk.chunk_id for m in matches]
    assert ids == ["a", "m", "z"]


def test_search_resultado_es_vector_match() -> None:
    store = InMemoryVectorStore()
    store.ensure_collection("c", dim=2)
    store.upsert("c", [_chunk("a", (1.0, 0.0))])
    [match] = store.search(
        "c",
        query_embedding=(1.0, 0.0),
        query=VectorQuery(text="dummy"),
    )
    assert isinstance(match, VectorMatch)
    assert match.chunk.embedding == (1.0, 0.0)
