"""Tests del proveedor determinista de embeddings."""

from __future__ import annotations

import math

import pytest

from hacienda_ai.rag.vector import (
    DeterministicHashEmbeddings,
    EmbeddingProviderError,
)


def test_dimension_correcta() -> None:
    provider = DeterministicHashEmbeddings(dim=128)
    vec = provider.embed_query("hola mundo")
    assert len(vec) == 128
    assert provider.dim == 128


def test_modelo_id_codifica_dim() -> None:
    provider = DeterministicHashEmbeddings(dim=256)
    assert provider.model_id == "deterministic-hash-256"


def test_embed_es_determinista() -> None:
    """Mismo texto → mismo vector entre llamadas e instancias."""
    p1 = DeterministicHashEmbeddings(dim=64)
    p2 = DeterministicHashEmbeddings(dim=64)
    v1 = p1.embed_query("contribuyente IRPF")
    v2 = p2.embed_query("contribuyente IRPF")
    assert v1 == v2


def test_embed_documents_es_paralelo_a_embed_query() -> None:
    """`embed_documents` y `embed_query` producen el mismo vector para
    el mismo texto (proveedor simétrico)."""
    provider = DeterministicHashEmbeddings(dim=64)
    [doc_vec] = provider.embed_documents(["texto"])
    query_vec = provider.embed_query("texto")
    assert doc_vec == query_vec


def test_vector_normalizado_norma_uno() -> None:
    """Salvo vectores cero, la salida está normalizada L2."""
    provider = DeterministicHashEmbeddings(dim=64)
    vec = provider.embed_query("alguna palabra")
    norm = math.sqrt(sum(v * v for v in vec))
    assert math.isclose(norm, 1.0, abs_tol=1e-9)


def test_embed_documents_devuelve_lista_paralela() -> None:
    # Dim alta para reducir probabilidad de colisión de hash entre
    # palabras distintas.
    provider = DeterministicHashEmbeddings(dim=2048)
    vectors = provider.embed_documents(["uno", "dos", "tres"])
    assert len(vectors) == 3
    assert all(len(v) == 2048 for v in vectors)
    assert len(set(vectors)) == 3


def test_texto_vacio_devuelve_vector_cero() -> None:
    provider = DeterministicHashEmbeddings(dim=64)
    vec = provider.embed_query("")
    assert all(v == 0.0 for v in vec)


def test_dim_minima_validada() -> None:
    with pytest.raises(EmbeddingProviderError):
        DeterministicHashEmbeddings(dim=4)


def test_palabras_solapadas_producen_cosine_alta() -> None:
    """Dos textos con palabras en común deben tener cosine > 0."""
    provider = DeterministicHashEmbeddings(dim=512)
    a = provider.embed_query("rendimientos del trabajo IRPF")
    b = provider.embed_query("rendimientos del capital IRPF")
    cos = sum(x * y for x, y in zip(a, b, strict=True))
    assert cos > 0  # comparten "rendimientos", "del", "IRPF".


def test_palabras_disjuntas_producen_cosine_cero() -> None:
    """Textos sin palabras en común → cosine = 0 (sin colisiones de hash)."""
    provider = DeterministicHashEmbeddings(dim=4096)
    a = provider.embed_query("xyzzy plugh")
    b = provider.embed_query("frobnitz quux")
    cos = sum(x * y for x, y in zip(a, b, strict=True))
    assert cos == 0.0
