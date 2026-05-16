"""Tests del cliente HTTP Voyage para embeddings.

Mockeamos `urllib.request.urlopen` para verificar:

- POST con cuerpo JSON correcto.
- Header `Authorization: Bearer <key>`.
- Batching automático.
- Reintentos con backoff en 429/5xx.
- 4xx (401, 400) → `EmbeddingProviderError` sin reintento.
- API key vía env var o argumento.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request

import pytest

from hacienda_ai.rag.vector import EmbeddingProviderError, VoyageEmbeddings


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


def _response_json(vectors: list[list[float]]) -> bytes:
    payload = {
        "object": "list",
        "data": [
            {"object": "embedding", "embedding": v, "index": i}
            for i, v in enumerate(vectors)
        ],
        "model": "voyage-law-2",
        "usage": {"total_tokens": 10},
    }
    return json.dumps(payload).encode("utf-8")


def _client(opener: _FakeOpener, *, dim: int = 4) -> VoyageEmbeddings:
    return VoyageEmbeddings(
        api_key="test-key",
        dim=dim,
        opener=opener,
        sleeper=lambda _: None,
        batch_size=2,
    )


def test_embed_query_post_correcto() -> None:
    opener = _FakeOpener([_response_json([[0.1, 0.2, 0.3, 0.4]])])
    client = _client(opener)
    vec = client.embed_query("¿Es deducible el gasto X?")
    assert vec == (0.1, 0.2, 0.3, 0.4)
    assert len(opener.calls) == 1
    req = opener.calls[0]
    assert req.method == "POST"
    assert req.headers["Authorization"] == "Bearer test-key"
    assert req.headers["Content-type"] == "application/json"
    body = json.loads(req.data.decode("utf-8"))  # type: ignore[union-attr]
    assert body["model"] == "voyage-law-2"
    assert body["input_type"] == "query"
    assert body["input"] == ["¿Es deducible el gasto X?"]


def test_embed_documents_usa_input_type_document() -> None:
    opener = _FakeOpener([_response_json([[1.0, 0.0, 0.0, 0.0]])])
    client = _client(opener)
    [_vec] = client.embed_documents(["chunk de manual"])
    body = json.loads(opener.calls[0].data.decode("utf-8"))  # type: ignore[union-attr]
    assert body["input_type"] == "document"


def test_batching_separa_peticiones() -> None:
    """Con batch_size=2 y 3 textos, debe haber 2 peticiones."""
    opener = _FakeOpener(
        [
            _response_json([[1.0, 0, 0, 0], [0, 1.0, 0, 0]]),  # primer batch
            _response_json([[0, 0, 1.0, 0]]),  # segundo batch
        ]
    )
    client = _client(opener)
    vecs = client.embed_documents(["a", "b", "c"])
    assert len(vecs) == 3
    assert len(opener.calls) == 2


def test_reintento_en_429() -> None:
    err = urllib.error.HTTPError(
        url="x", code=429, msg="Too Many Requests", hdrs=None, fp=None  # type: ignore[arg-type]
    )
    opener = _FakeOpener([err, _response_json([[1.0, 0, 0, 0]])])
    client = _client(opener)
    vec = client.embed_query("texto")
    assert vec == (1.0, 0.0, 0.0, 0.0)
    assert len(opener.calls) == 2


def test_reintento_en_500() -> None:
    err = urllib.error.HTTPError(
        url="x", code=503, msg="busy", hdrs=None, fp=None  # type: ignore[arg-type]
    )
    opener = _FakeOpener([err, err, _response_json([[1.0, 0, 0, 0]])])
    client = _client(opener)
    vec = client.embed_query("texto")
    assert vec == (1.0, 0.0, 0.0, 0.0)
    assert len(opener.calls) == 3


def test_401_no_se_reintenta() -> None:
    err = urllib.error.HTTPError(
        url="x",
        code=401,
        msg="Unauthorized",
        hdrs=None,  # type: ignore[arg-type]
        fp=io.BytesIO(b'{"error": "invalid api key"}'),
    )
    opener = _FakeOpener([err])
    client = _client(opener)
    with pytest.raises(EmbeddingProviderError) as exc_info:
        client.embed_query("texto")
    assert "401" in str(exc_info.value)
    assert len(opener.calls) == 1  # sin reintento.


def test_api_key_via_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VOYAGE_API_KEY", "env-key-123")
    opener = _FakeOpener([_response_json([[1.0, 0, 0, 0]])])
    client = VoyageEmbeddings(
        dim=4, opener=opener, sleeper=lambda _: None
    )
    client.embed_query("texto")
    assert opener.calls[0].headers["Authorization"] == "Bearer env-key-123"


def test_falta_api_key_lanza_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    opener = _FakeOpener([])
    client = VoyageEmbeddings(dim=4, opener=opener, sleeper=lambda _: None)
    with pytest.raises(EmbeddingProviderError) as exc_info:
        client.embed_query("texto")
    assert "VOYAGE_API_KEY" in str(exc_info.value)


def test_dim_inesperada_lanza() -> None:
    """Si el endpoint devuelve un vector de dim distinta a la configurada,
    fallamos: probablemente el `model` y el `dim` están desalineados."""
    opener = _FakeOpener([_response_json([[1.0, 0, 0]])])  # dim=3, esperaba 4
    client = _client(opener, dim=4)
    with pytest.raises(EmbeddingProviderError) as exc_info:
        client.embed_query("texto")
    assert "dimensión" in str(exc_info.value).lower()


def test_data_inesperada_lanza() -> None:
    bad_payload = json.dumps({"data": "not a list"}).encode("utf-8")
    opener = _FakeOpener([bad_payload])
    client = _client(opener)
    with pytest.raises(EmbeddingProviderError):
        client.embed_query("texto")


def test_modelo_id_default() -> None:
    client = VoyageEmbeddings(api_key="x", sleeper=lambda _: None)
    assert client.model_id == "voyage-law-2"
    assert client.dim == 1024
