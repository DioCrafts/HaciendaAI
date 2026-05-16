"""Embeddings con Voyage AI (`voyage-law-2`).

Voyage AI ofrece varios modelos de embeddings; `voyage-law-2` está
entrenado sobre corpus legal y rinde mejor que embeddings genéricos
para nuestro dominio fiscal/jurídico.

API REST:

    POST https://api.voyageai.com/v1/embeddings
    Authorization: Bearer <VOYAGE_API_KEY>
    {
      "input": ["texto 1", "texto 2", ...],
      "model": "voyage-law-2",
      "input_type": "document" | "query"
    }

Respuesta:

    {
      "object": "list",
      "data": [{"object": "embedding", "embedding": [...], "index": 0}, ...],
      "model": "voyage-law-2",
      "usage": {"total_tokens": ...}
    }

Voyage soporta input_type asimétrico: usar "document" al indexar el
corpus y "query" al embeber la consulta del usuario. Esto mejora el
retrieval respecto a un esquema simétrico.

Batching: la API admite hasta 128 textos por petición y ~120k tokens
totales. Este cliente batchea automáticamente con cuidado de no exceder
límites.

Reintentos: 3 con backoff exponencial ante 429/5xx. 4xx (401, 400)
no se reintenta — son errores definitivos.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from http.client import HTTPResponse
from typing import Callable, Protocol

from .provider import EmbeddingProviderError

# Dimensión real del modelo. Voyage la documenta; la fijamos para
# detectar configuraciones erróneas (si el endpoint devuelve otra dim,
# `dim` no coincidirá con el embedding y lanzamos).
_VOYAGE_LAW_2_DIM = 1024
_DEFAULT_MODEL = "voyage-law-2"
_DEFAULT_ENDPOINT = "https://api.voyageai.com/v1/embeddings"

# Voyage acepta hasta 128 textos/petición. Mantenemos margen.
_DEFAULT_BATCH_SIZE = 64
_DEFAULT_TIMEOUT = 60.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_RATE_LIMIT_SECONDS = 0.5


class _Opener(Protocol):
    """Interfaz mínima del opener HTTP para inyección en tests."""

    def open(
        self, req: urllib.request.Request, timeout: float
    ) -> HTTPResponse: ...


class VoyageEmbeddings:
    """Cliente HTTP de Voyage AI para embeddings.

    `api_key` se lee del argumento o de `$VOYAGE_API_KEY`. Si falta,
    `embed_*` lanza al primer uso (no en `__init__`) para que la
    creación del cliente no requiera la clave (útil en testing con
    opener mockeado).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        endpoint: str = _DEFAULT_ENDPOINT,
        dim: int = _VOYAGE_LAW_2_DIM,
        batch_size: int = _DEFAULT_BATCH_SIZE,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        rate_limit_seconds: float = _DEFAULT_RATE_LIMIT_SECONDS,
        opener: _Opener | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.model_id = model
        self.dim = dim
        self.endpoint = endpoint
        self.batch_size = batch_size
        self.timeout = timeout
        self.max_retries = max_retries
        self.rate_limit_seconds = rate_limit_seconds
        self._explicit_key: str | None = api_key
        self._opener: _Opener | None = opener
        self._sleep: Callable[[float], None] = (
            sleeper if sleeper is not None else time.sleep
        )

    # ---------- API pública ----------

    def embed_documents(self, texts: list[str]) -> list[tuple[float, ...]]:
        return self._embed_batched(texts, input_type="document")

    def embed_query(self, text: str) -> tuple[float, ...]:
        result = self._embed_batched([text], input_type="query")
        return result[0]

    # ---------- Internals ----------

    def _api_key(self) -> str:
        key = self._explicit_key or os.environ.get("VOYAGE_API_KEY")
        if not key:
            raise EmbeddingProviderError(
                "VOYAGE_API_KEY no configurada. Pásala vía argumento "
                "`api_key=...` o exporta la variable de entorno."
            )
        return key

    def _embed_batched(
        self, texts: list[str], *, input_type: str
    ) -> list[tuple[float, ...]]:
        if not texts:
            return []
        out: list[tuple[float, ...]] = []
        for batch in _chunks(texts, self.batch_size):
            vectors = self._embed_one_batch(batch, input_type=input_type)
            out.extend(vectors)
            # Rate-limit entre batches reales (no afecta a un solo batch).
            if len(texts) > self.batch_size:
                self._sleep(self.rate_limit_seconds)
        return out

    def _embed_one_batch(
        self, batch: list[str], *, input_type: str
    ) -> list[tuple[float, ...]]:
        payload: dict[str, object] = {
            "input": batch,
            "model": self.model_id,
            "input_type": input_type,
        }
        body = self._post_with_retry(payload)
        data = body.get("data")
        if not isinstance(data, list) or len(data) != len(batch):
            raise EmbeddingProviderError(
                f"respuesta inesperada de Voyage: "
                f"data={type(data).__name__} len={len(data) if isinstance(data, list) else 'n/a'}"
            )
        vectors: list[tuple[float, ...]] = []
        for item in data:
            embedding = item.get("embedding")
            if not isinstance(embedding, list):
                raise EmbeddingProviderError(
                    f"respuesta inesperada de Voyage: embedding={type(embedding).__name__}"
                )
            if len(embedding) != self.dim:
                raise EmbeddingProviderError(
                    f"dimensión inesperada: esperaba {self.dim}, recibí {len(embedding)}. "
                    "¿`model` y `dim` están alineados?"
                )
            vectors.append(tuple(float(v) for v in embedding))
        return vectors

    def _post_with_retry(self, payload: dict[str, object]) -> dict[str, object]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._post(payload)
            except EmbeddingProviderError:
                # 4xx definitivo: no reintentar.
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                self._sleep(2**attempt)
        raise EmbeddingProviderError(
            f"POST a Voyage falló tras {self.max_retries} intentos: {last_error}"
        ) from last_error

    def _post(self, payload: dict[str, object]) -> dict[str, object]:
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key()}",
                "User-Agent": "hacienda-ai-voyage/0.1",
            },
            method="POST",
        )
        try:
            response: HTTPResponse
            if self._opener is not None:
                response = self._opener.open(req, timeout=self.timeout)
            else:
                response = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            # 429 (rate limit) y 5xx → reintentar (lanzamos URLError).
            if exc.code == 429 or 500 <= exc.code < 600:
                raise urllib.error.URLError(f"HTTP {exc.code}") from exc
            # 4xx definitivo (401, 400…): lanzamos EmbeddingProviderError.
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise EmbeddingProviderError(
                f"HTTP {exc.code} en {self.endpoint}: {exc.reason}. Body: {body_text[:500]}"
            ) from exc

        with response:
            raw = response.read()
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EmbeddingProviderError(
                f"respuesta no-JSON de Voyage: {exc}"
            ) from exc
        if not isinstance(decoded, dict):
            raise EmbeddingProviderError(
                f"respuesta JSON no-objeto de Voyage: {type(decoded).__name__}"
            )
        return decoded


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
