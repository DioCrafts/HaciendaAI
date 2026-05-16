"""Tests del cliente Qdrant. HTTP mockeado."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from hacienda_ai.rag.vector import (
    EmbeddedChunk,
    QdrantVectorStore,
    SourceType,
    VectorQuery,
    VectorStoreError,
)


class _FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._buf = io.BytesIO(payload)
        self.status = status

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


def _ok(body: dict) -> bytes:
    return json.dumps(body).encode("utf-8")


def _client(opener: _FakeOpener) -> QdrantVectorStore:
    return QdrantVectorStore(
        url="http://localhost:6333",
        opener=opener,
        sleeper=lambda _: None,
    )


def _chunk(
    chunk_id: str, embedding: tuple[float, ...], **metadata: object
) -> EmbeddedChunk:
    return EmbeddedChunk(
        chunk_id=chunk_id,
        source_type=SourceType.NORMA,
        text=f"texto de {chunk_id}",
        embedding=embedding,
        embedding_model="test",
        metadata=metadata,
    )


def test_ensure_collection_crea_si_no_existe() -> None:
    not_found = urllib.error.HTTPError(
        url="x", code=404, msg="Not Found", hdrs=None, fp=None  # type: ignore[arg-type]
    )
    opener = _FakeOpener([not_found, _ok({"result": {}})])
    client = _client(opener)
    client.ensure_collection("col", dim=4)
    # Dos llamadas: GET (404) → PUT (200).
    assert len(opener.calls) == 2
    assert opener.calls[0].method == "GET"
    assert opener.calls[1].method == "PUT"
    body = json.loads(opener.calls[1].data.decode("utf-8"))  # type: ignore[union-attr]
    assert body["vectors"]["size"] == 4
    assert body["vectors"]["distance"] == "Cosine"


def test_ensure_collection_idempotente_si_dim_coincide() -> None:
    get_response = _ok(
        {"result": {"config": {"params": {"vectors": {"size": 4}}}}}
    )
    opener = _FakeOpener([get_response])
    client = _client(opener)
    client.ensure_collection("col", dim=4)
    # Solo GET; no se crea segunda vez.
    assert len(opener.calls) == 1


def test_ensure_collection_dim_mismatch_lanza() -> None:
    get_response = _ok(
        {"result": {"config": {"params": {"vectors": {"size": 8}}}}}
    )
    opener = _FakeOpener([get_response])
    client = _client(opener)
    with pytest.raises(VectorStoreError) as exc_info:
        client.ensure_collection("col", dim=4)
    assert "dim" in str(exc_info.value).lower()


def test_upsert_envia_puntos_con_payload() -> None:
    opener = _FakeOpener([_ok({"result": {"status": "ok"}})])
    client = _client(opener)
    upserted = client.upsert(
        "col",
        [_chunk("c1", (1.0, 0.0), impuesto="irpf")],
    )
    assert upserted == 1
    body = json.loads(opener.calls[0].data.decode("utf-8"))  # type: ignore[union-attr]
    assert "points" in body
    point = body["points"][0]
    assert point["vector"] == [1.0, 0.0]
    payload = point["payload"]
    assert payload["chunk_id"] == "c1"
    assert payload["impuesto"] == "irpf"
    assert payload["source_type"] == "norma"
    assert payload["embedding_model"] == "test"


def test_upsert_id_es_uuid_estable() -> None:
    """El id Qdrant se deriva del chunk_id vía UUIDv5 — estable y determinista."""
    opener = _FakeOpener(
        [_ok({"result": {}}), _ok({"result": {}})]
    )
    client = _client(opener)
    client.upsert("col", [_chunk("c1", (1.0,))])
    client.upsert("col", [_chunk("c1", (0.0,))])  # mismo chunk_id otra vez.
    id_1 = json.loads(opener.calls[0].data.decode("utf-8"))["points"][0]["id"]  # type: ignore[union-attr]
    id_2 = json.loads(opener.calls[1].data.decode("utf-8"))["points"][0]["id"]  # type: ignore[union-attr]
    assert id_1 == id_2  # UUIDv5 estable.


def test_search_parsea_resultado_y_score() -> None:
    response_body = _ok(
        {
            "result": [
                {
                    "id": "uuid-1",
                    "score": 0.95,
                    "vector": [1.0, 0.0],
                    "payload": {
                        "chunk_id": "norma::X",
                        "source_type": "norma",
                        "embedding_model": "test",
                        "text": "texto X",
                        "impuesto": "irpf",
                    },
                }
            ]
        }
    )
    opener = _FakeOpener([response_body])
    client = _client(opener)
    matches = client.search(
        "col",
        query_embedding=(1.0, 0.0),
        query=VectorQuery(text="dummy", top_k=5),
    )
    assert len(matches) == 1
    assert matches[0].score == 0.95
    assert matches[0].chunk.chunk_id == "norma::X"
    assert matches[0].chunk.metadata.get("impuesto") == "irpf"


def test_search_envia_filtros_a_qdrant() -> None:
    opener = _FakeOpener([_ok({"result": []})])
    client = _client(opener)
    client.search(
        "col",
        query_embedding=(1.0, 0.0),
        query=VectorQuery(
            text="dummy",
            source_types=(SourceType.SENTENCIA,),
            impuesto="irpf",
        ),
    )
    body = json.loads(opener.calls[0].data.decode("utf-8"))  # type: ignore[union-attr]
    assert body["limit"] == 10
    must = body["filter"]["must"]
    # Hay 2 filtros: source_type y impuesto.
    assert any(
        f.get("key") == "source_type" and f["match"]["any"] == ["sentencia"]
        for f in must
    )
    assert any(
        f.get("key") == "impuesto" and f["match"]["value"] == "irpf"
        for f in must
    )


def test_search_envia_min_score_si_se_pasa() -> None:
    opener = _FakeOpener([_ok({"result": []})])
    client = _client(opener)
    client.search(
        "col",
        query_embedding=(1.0,),
        query=VectorQuery(text="dummy", min_score=0.7),
    )
    body = json.loads(opener.calls[0].data.decode("utf-8"))  # type: ignore[union-attr]
    assert body["score_threshold"] == 0.7


def test_count_devuelve_entero() -> None:
    opener = _FakeOpener([_ok({"result": {"count": 42}})])
    client = _client(opener)
    assert client.count("col") == 42


def test_delete_envia_ids() -> None:
    opener = _FakeOpener([_ok({"result": {}})])
    client = _client(opener)
    deleted = client.delete("col", ["a", "b"])
    assert deleted == 2
    body = json.loads(opener.calls[0].data.decode("utf-8"))  # type: ignore[union-attr]
    assert body["points"] == ["a", "b"]


def test_reintento_en_500() -> None:
    err = urllib.error.HTTPError(
        url="x", code=503, msg="busy", hdrs=None, fp=None  # type: ignore[arg-type]
    )
    opener = _FakeOpener([err, _ok({"result": {"count": 0}})])
    client = _client(opener)
    assert client.count("col") == 0
    assert len(opener.calls) == 2


def test_api_key_se_envia_en_header() -> None:
    opener = _FakeOpener([_ok({"result": {"count": 0}})])
    client = QdrantVectorStore(
        url="http://qdrant:6333",
        api_key="secret-key",
        opener=opener,
        sleeper=lambda _: None,
    )
    client.count("col")
    assert opener.calls[0].headers["Api-key"] == "secret-key"
