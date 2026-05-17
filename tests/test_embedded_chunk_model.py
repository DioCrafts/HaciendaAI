"""Tests de los tipos del pipeline de vectorización."""

from __future__ import annotations

from datetime import date

from hacienda_ai.rag.vector import (
    EmbeddedChunk,
    IndexableChunk,
    SourceType,
    VectorMatch,
    VectorQuery,
)


def test_indexable_chunk_minimo() -> None:
    chunk = IndexableChunk(
        chunk_id="sentencia::ECLI:ES:TS:2024:1234",
        source_type=SourceType.SENTENCIA,
        text="texto a embebido",
        metadata={"impuesto": "irpf"},
    )
    assert chunk.source_type == SourceType.SENTENCIA
    assert chunk.metadata == {"impuesto": "irpf"}


def test_embedded_chunk_vector_dim_property() -> None:
    chunk = EmbeddedChunk(
        chunk_id="x",
        source_type=SourceType.NORMA,
        text="texto",
        embedding=(0.1, 0.2, 0.3, 0.4),
        embedding_model="test-model",
        metadata={},
    )
    assert chunk.vector_dim == 4


def test_vector_query_defaults() -> None:
    query = VectorQuery(text="cuestión legal")
    assert query.top_k == 10
    assert query.source_types is None
    assert query.impuesto is None
    assert query.fecha_devengo is None
    assert query.min_score == 0.0


def test_vector_query_con_filtros() -> None:
    query = VectorQuery(
        text="cuestión",
        top_k=5,
        source_types=(SourceType.SENTENCIA, SourceType.CONSULTA_DGT),
        impuesto="irpf",
        fecha_devengo=date(2024, 1, 1),
        min_score=0.7,
    )
    assert query.top_k == 5
    assert SourceType.SENTENCIA in query.source_types
    assert query.fecha_devengo == date(2024, 1, 1)


def test_vector_match_score() -> None:
    chunk = EmbeddedChunk(
        chunk_id="x",
        source_type=SourceType.NORMA,
        text="t",
        embedding=(1.0,),
        embedding_model="m",
        metadata={},
    )
    match = VectorMatch(chunk=chunk, score=0.85)
    assert match.score == 0.85
    assert match.chunk.chunk_id == "x"


def test_source_type_es_serializable_como_string() -> None:
    """`SourceType` extends str, así que su `value` es directamente
    usable como JSON sin transformaciones."""
    assert SourceType.SENTENCIA.value == "sentencia"
    assert str(SourceType.MANUAL.value) == "manual"
