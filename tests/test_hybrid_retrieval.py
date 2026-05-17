"""Tests del retrieval híbrido: BM25 + denso + reranker + RRF."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from hacienda_ai.rag.hybrid import (
    BM25Retriever,
    CohereReranker,
    HybridRetriever,
    IdentityReranker,
    RerankerError,
    rrf_fuse,
)
from hacienda_ai.rag.hybrid.bm25 import tokenize
from hacienda_ai.rag.vector import (
    DeterministicHashEmbeddings,
    IndexableChunk,
    InMemoryVectorStore,
    SourceType,
    VectorQuery,
    index_corpus,
)

# ---------- tokenize / BM25 ----------


def test_tokenize_lower_y_acentos() -> None:
    tokens = tokenize("Artículo 19.2.e) LIRPF")
    assert "artículo" in tokens
    assert "19" in tokens
    assert "lirpf" in tokens
    # `\w+` divide por `.` y `)`.
    assert "2" in tokens
    assert "e" in tokens


def test_bm25_search_devuelve_top_k_por_score() -> None:
    bm25 = BM25Retriever()
    bm25.index(
        [
            ("a", "gastos de defensa jurídica deducibles del IRPF"),
            ("b", "rendimientos del capital inmobiliario"),
            ("c", "impuesto sobre el valor añadido transmisión bienes"),
        ]
    )
    results = bm25.search("gastos de defensa IRPF", top_k=3)
    assert results[0][0] == "a"  # match más fuerte.


def test_bm25_query_vacia_devuelve_lista_vacia() -> None:
    bm25 = BM25Retriever()
    bm25.index([("a", "texto")])
    assert bm25.search("", top_k=5) == []
    assert bm25.search("   ", top_k=5) == []


def test_bm25_corpus_vacio_no_lanza() -> None:
    bm25 = BM25Retriever()
    assert bm25.search("cualquier cosa", top_k=5) == []
    assert bm25.count() == 0


def test_bm25_indexa_idempotente_por_id() -> None:
    bm25 = BM25Retriever()
    bm25.index([("a", "texto antiguo")])
    bm25.index([("a", "texto nuevo completamente distinto")])
    assert bm25.count() == 1
    # La query relevante al nuevo texto debe rankear "a".
    results = bm25.search("nuevo", top_k=5)
    assert results and results[0][0] == "a"


def test_bm25_delete_quita_chunks() -> None:
    bm25 = BM25Retriever()
    bm25.index([("a", "texto"), ("b", "otro texto")])
    bm25.delete(["a", "no-existe"])
    assert bm25.count() == 1
    assert all(cid != "a" for cid, _ in bm25.search("texto", top_k=5))


def test_bm25_normalizacion_longitud_documento() -> None:
    """Documentos cortos con el término deben rankear más alto que
    documentos muy largos con la misma frecuencia del término."""
    bm25 = BM25Retriever()
    bm25.index(
        [
            ("corto", "gastos deducibles"),
            ("largo", "gastos deducibles " + "lorem ipsum " * 100),
        ]
    )
    results = bm25.search("gastos deducibles", top_k=2)
    # El corto debe ir primero por la normalización por longitud.
    assert results[0][0] == "corto"


def test_bm25_idf_castiga_terminos_frecuentes() -> None:
    """Un término que aparece en todos los documentos da IDF bajo."""
    bm25 = BM25Retriever()
    bm25.index(
        [
            ("a", "el contribuyente español tiene rendimientos"),
            ("b", "el contribuyente declara su patrimonio"),
            ("c", "rendimientos del trabajo en IRPF"),
        ]
    )
    # "el" aparece en todos: IDF ≈ 0. "IRPF" solo en c: IDF alto.
    results = bm25.search("el IRPF", top_k=3)
    assert results[0][0] == "c"


# ---------- RRF ----------


def test_rrf_combina_dos_rankings() -> None:
    bm25_ranking = [("a", 5.0), ("b", 3.0), ("c", 1.0)]
    dense_ranking = [("b", 0.9), ("a", 0.8), ("d", 0.7)]
    fused = rrf_fuse([bm25_ranking, dense_ranking])
    fused_ids = [cid for cid, _ in fused]
    # `a` y `b` aparecen en ambos rankings altos: deben rankear primero.
    assert fused_ids[0] in {"a", "b"}
    assert fused_ids[1] in {"a", "b"}
    # `c` y `d` solo en uno, deben venir después.
    assert set(fused_ids[2:]) == {"c", "d"}


def test_rrf_un_solo_ranking_preserva_orden() -> None:
    ranking = [("a", 1.0), ("b", 0.5), ("c", 0.1)]
    fused = rrf_fuse([ranking])
    fused_ids = [cid for cid, _ in fused]
    assert fused_ids == ["a", "b", "c"]


def test_rrf_lista_vacia_devuelve_vacio() -> None:
    assert rrf_fuse([]) == []
    assert rrf_fuse([[]]) == []


# ---------- IdentityReranker ----------


def test_identity_reranker_preserva_orden() -> None:
    reranker = IdentityReranker()
    out = reranker.rerank(
        query="x",
        candidates=[("a", "txt a", 0.9), ("b", "txt b", 0.5)],
        top_k=2,
    )
    assert out == [("a", 0.9), ("b", 0.5)]


def test_identity_reranker_trunca_a_top_k() -> None:
    reranker = IdentityReranker()
    out = reranker.rerank(
        query="x",
        candidates=[("a", "t", 1.0), ("b", "t", 0.5), ("c", "t", 0.1)],
        top_k=2,
    )
    assert len(out) == 2


# ---------- CohereReranker ----------


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._buf = io.BytesIO(payload)
        self.status = 200

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self._buf.close()


class _FakeOpener:
    def __init__(self, queue: list[bytes | Exception]) -> None:
        self.queue = queue
        self.calls: list[urllib.request.Request] = []

    def open(self, req: urllib.request.Request, timeout: float) -> _FakeResponse:
        self.calls.append(req)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


def _cohere_response(scores_by_index: list[tuple[int, float]]) -> bytes:
    payload = {
        "results": [
            {"index": idx, "relevance_score": score}
            for idx, score in scores_by_index
        ]
    }
    return json.dumps(payload).encode("utf-8")


def test_cohere_reranker_reordena_segun_relevancia() -> None:
    """Cohere puede devolver índices en otro orden; el cliente
    los traduce a chunk_ids correctos."""
    opener = _FakeOpener(
        [_cohere_response([(1, 0.95), (0, 0.30)])]
    )
    reranker = CohereReranker(
        api_key="test", opener=opener, sleeper=lambda _: None
    )
    out = reranker.rerank(
        query="x",
        candidates=[("a", "txt", 0.5), ("b", "txt", 0.5)],
        top_k=2,
    )
    assert out == [("b", 0.95), ("a", 0.30)]


def test_cohere_reranker_envia_payload_correcto() -> None:
    opener = _FakeOpener([_cohere_response([(0, 1.0)])])
    reranker = CohereReranker(
        api_key="abc123", opener=opener, sleeper=lambda _: None
    )
    reranker.rerank(
        query="¿son deducibles?",
        candidates=[("a", "texto del chunk a", 0.5)],
        top_k=10,
    )
    body = json.loads(opener.calls[0].data.decode("utf-8"))  # type: ignore[union-attr]
    assert body["model"] == "rerank-multilingual-v3.0"
    assert body["query"] == "¿son deducibles?"
    assert body["documents"] == ["texto del chunk a"]
    assert opener.calls[0].headers["Authorization"] == "Bearer abc123"


def test_cohere_reranker_401_lanza() -> None:
    err = urllib.error.HTTPError(
        url="x",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b'{"error":"invalid"}'),
    )
    opener = _FakeOpener([err])
    reranker = CohereReranker(
        api_key="bad", opener=opener, sleeper=lambda _: None
    )
    with pytest.raises(RerankerError):
        reranker.rerank(
            query="x",
            candidates=[("a", "t", 0.5)],
            top_k=1,
        )


def test_cohere_reranker_reintenta_en_429() -> None:
    err = urllib.error.HTTPError(
        url="x", code=429, msg="Rate Limited", hdrs=None, fp=None  # type: ignore[arg-type]
    )
    opener = _FakeOpener([err, _cohere_response([(0, 0.9)])])
    reranker = CohereReranker(
        api_key="x", opener=opener, sleeper=lambda _: None
    )
    out = reranker.rerank(
        query="x", candidates=[("a", "t", 0.5)], top_k=1
    )
    assert out == [("a", 0.9)]
    assert len(opener.calls) == 2


def test_cohere_reranker_candidates_vacios_no_llama_api() -> None:
    opener = _FakeOpener([])
    reranker = CohereReranker(
        api_key="x", opener=opener, sleeper=lambda _: None
    )
    out = reranker.rerank(query="x", candidates=[], top_k=10)
    assert out == []
    assert opener.calls == []


# ---------- HybridRetriever integración ----------


def _build_hybrid_corpus() -> tuple[
    BM25Retriever,
    InMemoryVectorStore,
    DeterministicHashEmbeddings,
]:
    provider = DeterministicHashEmbeddings(dim=512)
    store = InMemoryVectorStore()
    chunks = [
        IndexableChunk(
            chunk_id="norma::lirpf::a19_2e",
            source_type=SourceType.NORMA,
            text=(
                "Los gastos de defensa jurídica derivados directamente de "
                "litigios en la relación del contribuyente con la persona "
                "de la que percibe los rendimientos, con el límite de 300 "
                "euros anuales."
            ),
            metadata={"impuesto": "irpf", "articulo": "art. 19", "apartado": "2.e)"},
        ),
        IndexableChunk(
            chunk_id="dgt::V0123-24",
            source_type=SourceType.CONSULTA_DGT,
            text=(
                "Consulta DGT sobre gastos de defensa jurídica en "
                "procedimiento tributario. No son deducibles."
            ),
            metadata={"impuesto": "irpf"},
        ),
        IndexableChunk(
            chunk_id="norma::liva::a25",
            source_type=SourceType.NORMA,
            text=(
                "Entregas intracomunitarias exentas del IVA según el "
                "artículo 25 de la Ley 37/1992."
            ),
            metadata={"impuesto": "iva"},
        ),
    ]
    index_corpus(
        chunks,
        collection="test",
        provider=provider,
        store=store,
    )
    bm25 = BM25Retriever()
    bm25.index([(c.chunk_id, c.text) for c in chunks])
    return bm25, store, provider


def test_hybrid_retriever_combina_bm25_y_denso() -> None:
    bm25, store, provider = _build_hybrid_corpus()
    retriever = HybridRetriever(
        bm25=bm25,
        vector_store=store,
        provider=provider,
        collection="test",
    )
    matches = retriever.search(
        VectorQuery(text="gastos defensa jurídica IRPF", top_k=2)
    )
    assert len(matches) >= 1
    # El primer resultado debería ser uno de los chunks IRPF.
    assert matches[0].chunk.metadata.get("impuesto") == "irpf"


def test_hybrid_retriever_aplica_filtro_impuesto() -> None:
    bm25, store, provider = _build_hybrid_corpus()
    retriever = HybridRetriever(
        bm25=bm25, vector_store=store, provider=provider, collection="test"
    )
    matches = retriever.search(
        VectorQuery(text="entregas", top_k=5, impuesto="iva")
    )
    assert all(
        m.chunk.metadata.get("impuesto") == "iva" for m in matches
    )


def test_hybrid_retriever_aplica_filtro_source_type() -> None:
    bm25, store, provider = _build_hybrid_corpus()
    retriever = HybridRetriever(
        bm25=bm25, vector_store=store, provider=provider, collection="test"
    )
    matches = retriever.search(
        VectorQuery(
            text="gastos defensa",
            top_k=5,
            source_types=(SourceType.CONSULTA_DGT,),
        )
    )
    assert all(
        m.chunk.source_type == SourceType.CONSULTA_DGT for m in matches
    )


def test_hybrid_retriever_reranker_falla_degrada_gracefully() -> None:
    """Si el reranker lanza, devolvemos el orden RRF en vez de abortar."""
    bm25, store, provider = _build_hybrid_corpus()

    class _FailingReranker:
        def rerank(self, **kwargs):  # type: ignore[no-untyped-def]
            raise RerankerError("API caída")

    retriever = HybridRetriever(
        bm25=bm25,
        vector_store=store,
        provider=provider,
        reranker=_FailingReranker(),  # type: ignore[arg-type]
        collection="test",
    )
    matches = retriever.search(
        VectorQuery(text="gastos defensa jurídica", top_k=3)
    )
    # Algunos resultados se devuelven aunque el reranker falle.
    assert len(matches) >= 1


def test_hybrid_retriever_min_score_filtra_resultados_pobres() -> None:
    bm25, store, provider = _build_hybrid_corpus()
    retriever = HybridRetriever(
        bm25=bm25, vector_store=store, provider=provider, collection="test"
    )
    # min_score altísimo: nada lo pasa con IdentityReranker (que
    # devuelve los scores RRF como float pequeño ~0.016).
    matches = retriever.search(
        VectorQuery(text="cualquier cosa", top_k=10, min_score=999.0)
    )
    assert matches == []
