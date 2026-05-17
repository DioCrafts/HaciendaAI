"""Re-rankers: reordena candidatos del retriever híbrido con un modelo
que ve query y documento juntos.

Dos implementaciones del Protocol `Reranker`:

- **`IdentityReranker`**: devuelve el orden tal cual. Sin red, sin
  dependencias. Para CI/tests/demos donde no hay API key y queremos
  testear el pipeline sin reorden.

- **`CohereReranker`**: HTTP contra api.cohere.com con el endpoint
  `/v1/rerank` y el modelo `rerank-multilingual-v3.0` (soporta español
  bien). Requiere `COHERE_API_KEY`.

API:

    reranker = CohereReranker(api_key=...)
    reranked = reranker.rerank(
        query="¿son deducibles los gastos de defensa?",
        candidates=[(chunk_id_1, text_1, prior_score_1), ...],
        top_k=10,
    )
    # [(chunk_id, new_score), ...] ordenado por relevancia del reranker.

Política ante fallo del rerank: el caller decide. `HybridRetriever`
por defecto loguea el error y devuelve el orden original (mejor que
abortar la consulta del usuario).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from http.client import HTTPResponse
from typing import Callable, Protocol


class RerankerError(RuntimeError):
    """Error al rerankar (red, autenticación, formato)."""


class Reranker(Protocol):
    """Contrato: dada una query y candidatos, devolver orden reordenado."""

    def rerank(
        self,
        *,
        query: str,
        candidates: list[tuple[str, str, float]],
        top_k: int,
    ) -> list[tuple[str, float]]: ...


# ---------- IdentityReranker ----------


class IdentityReranker:
    """Devuelve los candidatos tal cual, truncados a `top_k`.

    Útil para CI/tests del pipeline cuando no queremos depender del
    reranker real. El orden de entrada se preserva.
    """

    def rerank(
        self,
        *,
        query: str,
        candidates: list[tuple[str, str, float]],
        top_k: int,
    ) -> list[tuple[str, float]]:
        del query  # no usamos la query en el identity reranker.
        return [(cid, score) for cid, _text, score in candidates[:top_k]]


# ---------- CohereReranker ----------


_DEFAULT_MODEL = "rerank-multilingual-v3.0"
_DEFAULT_ENDPOINT = "https://api.cohere.com/v1/rerank"
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_MAX_RETRIES = 3


class _Opener(Protocol):
    """Interfaz mínima del opener HTTP para inyección en tests."""

    def open(
        self, req: urllib.request.Request, timeout: float
    ) -> HTTPResponse: ...


class CohereReranker:
    """Cliente HTTP de Cohere Rerank.

    `api_key` se lee del argumento o de `$COHERE_API_KEY`. Si falta,
    `rerank` lanza al primer uso (no en `__init__`) — coherente con
    el resto de proveedores del repo.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        endpoint: str = _DEFAULT_ENDPOINT,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        opener: _Opener | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.model = model
        self.endpoint = endpoint
        self.timeout = timeout
        self.max_retries = max_retries
        self._explicit_key = api_key
        self._opener = opener
        self._sleep: Callable[[float], None] = (
            sleeper if sleeper is not None else time.sleep
        )

    def rerank(
        self,
        *,
        query: str,
        candidates: list[tuple[str, str, float]],
        top_k: int,
    ) -> list[tuple[str, float]]:
        if not candidates:
            return []
        documents = [text for _, text, _ in candidates]
        payload: dict[str, object] = {
            "model": self.model,
            "query": query,
            "documents": documents,
            "top_n": min(top_k, len(candidates)),
        }
        body = self._post_with_retry(payload)
        results = body.get("results")
        if not isinstance(results, list):
            raise RerankerError(
                f"respuesta inesperada de Cohere rerank: results={type(results).__name__}"
            )
        out: list[tuple[str, float]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            idx = item.get("index")
            score = item.get("relevance_score")
            if not isinstance(idx, int) or not isinstance(score, (int, float)):
                continue
            if 0 <= idx < len(candidates):
                cid, _text, _ = candidates[idx]
                out.append((cid, float(score)))
        return out

    # ---------- Internals ----------

    def _api_key(self) -> str:
        key = self._explicit_key or os.environ.get("COHERE_API_KEY")
        if not key:
            raise RerankerError(
                "COHERE_API_KEY no configurada. Pásala vía argumento o "
                "exporta la variable de entorno."
            )
        return key

    def _post_with_retry(self, payload: dict[str, object]) -> dict[str, object]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._post(payload)
            except RerankerError:
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                self._sleep(2**attempt)
        raise RerankerError(
            f"POST a Cohere falló tras {self.max_retries} intentos: {last_error}"
        ) from last_error

    def _post(self, payload: dict[str, object]) -> dict[str, object]:
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key()}",
                "User-Agent": "hacienda-ai-cohere/0.1",
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
            if exc.code == 429 or 500 <= exc.code < 600:
                raise urllib.error.URLError(f"HTTP {exc.code}") from exc
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise RerankerError(
                f"HTTP {exc.code} en {self.endpoint}: {exc.reason}. Body: {body_text[:500]}"
            ) from exc

        with response:
            raw = response.read()
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RerankerError(f"respuesta no-JSON: {exc}") from exc
        if not isinstance(decoded, dict):
            raise RerankerError(f"respuesta no-objeto: {type(decoded).__name__}")
        return decoded
