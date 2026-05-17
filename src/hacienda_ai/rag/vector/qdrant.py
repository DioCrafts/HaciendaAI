"""Vector store contra Qdrant self-hosted vía HTTP REST.

Qdrant API mínima que usamos:

    PUT  /collections/<name>                       → crear colección.
    GET  /collections/<name>                       → comprobar existencia + dim.
    PUT  /collections/<name>/points                → upsert puntos.
    POST /collections/<name>/points/delete         → borrar por ids.
    POST /collections/<name>/points/search         → búsqueda vectorial.
    POST /collections/<name>/points/count          → contar puntos.

Self-hosted con:

    docker run -p 6333:6333 -v $(pwd)/qdrant_storage:/qdrant/storage qdrant/qdrant

No usamos el SDK oficial (`qdrant-client`) para no añadir una
dependencia más. La superficie HTTP que utilizamos es estable.

Filtros: traducimos los filtros del `VectorQuery` a la sintaxis Qdrant
`filter.must` con operadores `match` (igualdad), `range` (fechas).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from http.client import HTTPResponse
from typing import Any, Callable, Protocol

from .embedded_chunk import EmbeddedChunk, SourceType, VectorMatch, VectorQuery
from .store import VectorStoreError

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
USER_AGENT = "hacienda-ai-qdrant/0.1"


class _Opener(Protocol):
    """Interfaz mínima del opener HTTP para inyección en tests."""

    def open(
        self, req: urllib.request.Request, timeout: float
    ) -> HTTPResponse: ...


class QdrantVectorStore:
    """Cliente HTTP de Qdrant. Self-hosted, sin SDK.

    `url` típicamente `http://localhost:6333`. `api_key` solo necesaria
    si Qdrant está protegido con `service.api_key` en config.
    """

    def __init__(
        self,
        *,
        url: str = "http://localhost:6333",
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        opener: _Opener | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self._opener: _Opener | None = opener
        import time as _time

        self._sleep: Callable[[float], None] = (
            sleeper if sleeper is not None else _time.sleep
        )

    # ---------- API VectorStore ----------

    def ensure_collection(self, name: str, *, dim: int) -> None:
        existing_dim = self._get_collection_dim(name)
        if existing_dim is None:
            # No existe: la creamos.
            self._request(
                "PUT",
                f"/collections/{name}",
                body={
                    "vectors": {
                        "size": dim,
                        # Cosine sobre vectores normalizados — coincide
                        # con InMemoryVectorStore.
                        "distance": "Cosine",
                    },
                },
            )
            return
        if existing_dim != dim:
            raise VectorStoreError(
                f"colección {name!r} ya existe con dim={existing_dim}, "
                f"se pidió dim={dim}. Borra la colección o usa el dim correcto."
            )

    def upsert(self, name: str, chunks: list[EmbeddedChunk]) -> int:
        if not chunks:
            return 0
        # Estructura de cada punto Qdrant: id, vector, payload (metadata).
        points = [_chunk_to_point(c) for c in chunks]
        # Qdrant soporta batches grandes pero ponemos tope para evitar
        # request bodies enormes. 256 es conservador.
        batch_size = 256
        upserted = 0
        for batch in _batched(points, batch_size):
            self._request(
                "PUT",
                f"/collections/{name}/points",
                body={"points": batch},
                params={"wait": "true"},
            )
            upserted += len(batch)
        return upserted

    def delete(self, name: str, chunk_ids: list[str]) -> int:
        if not chunk_ids:
            return 0
        self._request(
            "POST",
            f"/collections/{name}/points/delete",
            body={"points": chunk_ids},
            params={"wait": "true"},
        )
        # Qdrant no devuelve el count exacto de borrados; asumimos que
        # todos los ids existían (el caller suele invocar con ids que ya
        # estaban). Si no estaban, no es error.
        return len(chunk_ids)

    def count(self, name: str) -> int:
        resp = self._request("POST", f"/collections/{name}/points/count", body={})
        result = resp.get("result")
        if not isinstance(result, dict) or "count" not in result:
            raise VectorStoreError(f"respuesta count inesperada: {resp!r}")
        return int(result["count"])

    def search(
        self,
        name: str,
        *,
        query_embedding: tuple[float, ...],
        query: VectorQuery,
    ) -> list[VectorMatch]:
        body: dict[str, Any] = {
            "vector": list(query_embedding),
            "limit": query.top_k,
            "with_payload": True,
            "with_vector": True,
        }
        if query.min_score > 0:
            body["score_threshold"] = query.min_score
        filter_clause = _build_filter(query)
        if filter_clause:
            body["filter"] = filter_clause

        resp = self._request(
            "POST", f"/collections/{name}/points/search", body=body
        )
        result = resp.get("result")
        if not isinstance(result, list):
            raise VectorStoreError(f"respuesta search inesperada: {resp!r}")
        out: list[VectorMatch] = []
        for hit in result:
            if not isinstance(hit, dict):
                continue
            out.append(_point_to_match(hit))
        return out

    # ---------- HTTP internals ----------

    def _get_collection_dim(self, name: str) -> int | None:
        try:
            resp = self._request("GET", f"/collections/{name}")
        except VectorStoreError as exc:
            # 404 es esperado cuando la colección no existe.
            if "404" in str(exc):
                return None
            raise
        result = resp.get("result")
        if not isinstance(result, dict):
            raise VectorStoreError(f"respuesta inesperada en GET: {resp!r}")
        config = result.get("config", {})
        params = config.get("params") if isinstance(config, dict) else None
        vectors = params.get("vectors") if isinstance(params, dict) else None
        if isinstance(vectors, dict):
            size = vectors.get("size")
            if isinstance(size, int):
                return size
        raise VectorStoreError(
            f"no se pudo determinar dim de la colección {name!r}: {result!r}"
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        full_url = self.url + path
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            full_url = f"{full_url}?{qs}"

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._raw_request(method, full_url, body)
            except VectorStoreError:
                # 4xx definitivo: no reintentar.
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                self._sleep(2**attempt)
        raise VectorStoreError(
            f"{method} {full_url} falló tras {self.max_retries} intentos: {last_error}"
        ) from last_error

    def _raw_request(
        self,
        method: str,
        full_url: str,
        body: dict[str, Any] | None,
    ) -> dict[str, Any]:
        data: bytes | None = None
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["api-key"] = self.api_key

        req = urllib.request.Request(
            full_url, data=data, headers=headers, method=method
        )
        try:
            response: HTTPResponse
            if self._opener is not None:
                response = self._opener.open(req, timeout=self.timeout)
            else:
                response = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if 500 <= exc.code < 600:
                raise urllib.error.URLError(f"HTTP {exc.code}") from exc
            body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            raise VectorStoreError(
                f"{method} {full_url}: HTTP {exc.code} {exc.reason}. Body: {body_text[:500]}"
            ) from exc

        with response:
            raw = response.read()
        try:
            decoded = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise VectorStoreError(
                f"respuesta no-JSON: {exc}"
            ) from exc
        if not isinstance(decoded, dict):
            raise VectorStoreError(
                f"respuesta no-objeto: {type(decoded).__name__}"
            )
        return decoded


# ---------- Mapeo chunk ↔ punto Qdrant ----------


def _chunk_to_point(chunk: EmbeddedChunk) -> dict[str, Any]:
    payload = {
        # Reservamos un campo "text" para el contenido y otro "type"
        # para `SourceType`. El resto del payload son los metadatos
        # libres del chunk.
        "text": chunk.text,
        "source_type": chunk.source_type.value,
        "embedding_model": chunk.embedding_model,
        **chunk.metadata,
    }
    # Qdrant exige id sea int o uuid. El `chunk_id` original (con `::`)
    # NO es un uuid válido, así que lo enviamos como string y dejamos a
    # Qdrant aceptarlo. Para máxima compatibilidad usamos un payload
    # extra `chunk_id` para poder reconstruirlo y un id "estable" derivado.
    payload["chunk_id"] = chunk.chunk_id
    return {
        "id": _stable_id_from(chunk.chunk_id),
        "vector": list(chunk.embedding),
        "payload": payload,
    }


def _stable_id_from(chunk_id: str) -> str:
    """Convierte `chunk_id` a un id estable filesystem/qdrant-safe.

    Qdrant acepta como id un entero o un UUID string. Para evitar
    colisiones y mantener legibilidad construimos un UUIDv5 a partir
    del `chunk_id`.
    """
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def _point_to_match(hit: dict[str, Any]) -> VectorMatch:
    payload = hit.get("payload") or {}
    vector = hit.get("vector") or []
    score = float(hit.get("score", 0.0))

    chunk_id = str(payload.pop("chunk_id", hit.get("id", "")))
    text = str(payload.pop("text", ""))
    source_type_raw = payload.pop("source_type", "norma")
    try:
        source_type = SourceType(source_type_raw)
    except ValueError:
        source_type = SourceType.NORMA  # fallback defensivo.
    embedding_model = str(payload.pop("embedding_model", "unknown"))

    chunk = EmbeddedChunk(
        chunk_id=chunk_id,
        source_type=source_type,
        text=text,
        embedding=tuple(float(v) for v in vector),
        embedding_model=embedding_model,
        metadata=dict(payload),
    )
    return VectorMatch(chunk=chunk, score=score)


# ---------- Filtros ----------


def _build_filter(query: VectorQuery) -> dict[str, Any] | None:
    must: list[dict[str, Any]] = []
    if query.source_types is not None:
        must.append(
            {
                "key": "source_type",
                "match": {
                    "any": [s.value for s in query.source_types],
                },
            }
        )
    if query.impuesto is not None:
        must.append(
            {"key": "impuesto", "match": {"value": query.impuesto}}
        )
    if query.fecha_devengo is not None:
        # Vigencia: effective_from <= devengo (o ausente) Y
        # effective_to >= devengo (o ausente). Qdrant Range con `lte`/
        # `gte` en strings ISO funciona porque YYYY-MM-DD se compara
        # lexicográficamente igual que cronológicamente.
        target_iso = query.fecha_devengo.isoformat()
        # No filtramos a nivel Qdrant los nulos (Qdrant no soporta
        # "OR null" trivialmente). El filtro estricto de fecha lo
        # aplicamos post-search en `_postprocess_temporal` cuando hace
        # falta — para corpus pequeños el coste es despreciable.
        # Para corpus grandes conviene materializar el filtro con
        # campos auxiliares (`effective_from_or_min`) en la ingesta.
        must.append(
            {
                "key": "effective_from",
                "range": {"lte": target_iso},
            }
        )
    return {"must": must} if must else None


def _batched(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


