"""Factoría del retriever híbrido para producción.

Ensambla un `HybridRetriever` listo para usar desde:

- **Programáticamente** (`build_retriever_from_config`): recibe una
  `RetrieverConfig` explícita, ideal para tests y para integradores que
  quieren control fino de los componentes (provider, store, reranker,
  parámetros de retrieval).

- **Desde el entorno** (`build_retriever_from_env`): lee variables del
  environment para activar/configurar el RAG sin tocar código. Devuelve
  `None` si el RAG no está habilitado o si la config esencial falta —
  el caller (`create_app`) decide qué hacer.

El diseño es estrictamente opt-in: si no se invoca o devuelve `None`,
el resto del sistema (chat, API) sigue funcionando exactamente como
antes (sin RAG), igual que el cableado anterior a Fase 1. No hay
sorpresas a la hora de desplegar: si quieres RAG, lo activas con
`HACIENDA_AI_RAG_ENABLED=1` y configuras la fuente del corpus.

Indexación: la factoría indexa el corpus en memoria (BM25 siempre,
vectores opcionalmente en InMemory) durante el arranque. Para
producción real con corpus grande se usa Qdrant: el operador
preindexa offline con `scripts/index_vector_store.py` y la factoría
construye un retriever que se conecta al Qdrant existente sin
reindexar (porque `index_corpus` no se llama si `auto_index=False`).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping

from .hybrid import BM25Retriever, HybridRetriever, IdentityReranker, Reranker
from .vector import (
    DeterministicHashEmbeddings,
    EmbeddingProvider,
    InMemoryVectorStore,
    QdrantVectorStore,
    VectorStore,
    VoyageEmbeddings,
    index_corpus,
    iter_corpus_chunks,
)

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "hacienda_corpus_v1"
DEFAULT_HASH_DIM = 1024
DEFAULT_BM25_TOP_K = 50
DEFAULT_DENSE_TOP_K = 50
DEFAULT_FUSION_TOP_K = 50

ProviderName = Literal["hash", "voyage"]
StoreName = Literal["memory", "qdrant"]


class RetrieverFactoryError(RuntimeError):
    """Configuración inválida para construir el retriever."""


@dataclass
class RetrieverConfig:
    """Configuración inmutable de la factoría.

    Campos mínimos: `data_dir` (raíz del corpus a indexar). Todo lo
    demás tiene defaults sensatos para arranque rápido sin red:
    `provider=hash` + `store=memory` montan un retriever totalmente
    local en segundos, útil para desarrollo y demos.

    Para producción real:
    - `provider="voyage"` + `voyage_api_key` → embeddings legales.
    - `store="qdrant"` + `qdrant_url` → vector store persistente.
    - `auto_index=False` → asume corpus preindexado por
      `scripts/index_vector_store.py`; la factoría solo construye el
      cliente y el BM25 local.
    """

    data_dir: Path
    collection: str = DEFAULT_COLLECTION
    provider: ProviderName = "hash"
    store: StoreName = "memory"
    qdrant_url: str | None = None
    qdrant_api_key: str | None = None
    voyage_api_key: str | None = None
    hash_dim: int = DEFAULT_HASH_DIM
    bm25_top_k: int = DEFAULT_BM25_TOP_K
    dense_top_k: int = DEFAULT_DENSE_TOP_K
    fusion_top_k: int = DEFAULT_FUSION_TOP_K
    auto_index: bool = True


@dataclass
class RetrieverBuildReport:
    """Telemetría del arranque del retriever.

    Útil para que el caller (típicamente `create_app`) registre en log
    cuántos chunks se indexaron, qué errores hubo (sin abortar) y qué
    provider/store quedó activo.
    """

    config: RetrieverConfig
    provider_model_id: str
    provider_dim: int
    store_kind: str
    total_chunks: int = 0
    indexed: int = 0
    errors: list[str] = field(default_factory=list)
    bm25_documents: int = 0


def build_retriever_from_config(
    config: RetrieverConfig,
) -> tuple[HybridRetriever, RetrieverBuildReport]:
    """Construye el HybridRetriever y devuelve también un informe de arranque.

    Pasos:
    1. Construye `EmbeddingProvider` (hash o Voyage).
    2. Construye `VectorStore` (InMemory o Qdrant).
    3. Itera el corpus desde `data_dir` y, si `auto_index=True`, lo
       indexa en el store vectorial. El BM25 local se indexa SIEMPRE
       (es barato y necesario para la rama léxica).
    4. Devuelve el `HybridRetriever` cableado.

    Si `data_dir` no existe o está vacío, no falla — devuelve el
    retriever con índices vacíos y un report con `total_chunks=0`. El
    operador puede entonces decidir si seguir o abortar.
    """
    provider = _build_provider(config)
    store = _build_store(config)
    bm25 = BM25Retriever()
    reranker: Reranker = IdentityReranker()
    report = RetrieverBuildReport(
        config=config,
        provider_model_id=provider.model_id,
        provider_dim=provider.dim,
        store_kind=type(store).__name__,
    )

    # Aseguramos la colección SIEMPRE — incluso con corpus vacío o
    # `auto_index=False`. Sin esto, una búsqueda posterior contra un
    # store recién creado lanzaría `VectorStoreError("colección X no
    # existe")`. La operación es idempotente: si la colección ya existe
    # con la dimensión correcta, no hace nada; si existe con otra
    # dimensión, lanza explícitamente (mejor fallar al arrancar que en
    # la primera query del usuario).
    store.ensure_collection(config.collection, dim=provider.dim)

    if not config.data_dir.exists():
        logger.warning(
            "RAG data_dir %s no existe; el retriever arranca con índices "
            "vacíos. Indexa el corpus o ajusta HACIENDA_AI_CORPUS_DIR.",
            config.data_dir,
        )
    else:
        chunks = list(iter_corpus_chunks(config.data_dir))
        report.total_chunks = len(chunks)
        if not chunks:
            logger.warning(
                "RAG data_dir %s no contiene chunks indexables; el "
                "retriever arranca con índices vacíos.",
                config.data_dir,
            )
        else:
            # BM25 local: siempre (rama léxica, sin coste externo).
            bm25_indexed = bm25.index([(c.chunk_id, c.text) for c in chunks])
            report.bm25_documents = bm25_indexed
            if config.auto_index:
                # Vector store: solo si auto_index. Si el operador
                # preindexó con scripts/index_vector_store.py, omitimos
                # esto para no duplicar el coste (Voyage cobra) ni
                # re-upsertear datos que ya están en Qdrant.
                index_report = index_corpus(
                    chunks,
                    collection=config.collection,
                    provider=provider,
                    store=store,
                )
                report.indexed = index_report.upserted
                report.errors = list(index_report.errors)

    retriever = HybridRetriever(
        bm25=bm25,
        vector_store=store,
        provider=provider,
        reranker=reranker,
        collection=config.collection,
        bm25_top_k=config.bm25_top_k,
        dense_top_k=config.dense_top_k,
        fusion_top_k=config.fusion_top_k,
    )
    return retriever, report


def build_retriever_from_env(
    env: Mapping[str, str] | None = None,
) -> tuple[HybridRetriever, RetrieverBuildReport] | None:
    """Lee variables de entorno y construye el retriever si está habilitado.

    Variables soportadas (todas opcionales; default = RAG desactivado):

    - `HACIENDA_AI_RAG_ENABLED`: `"1"`/`"true"`/`"yes"` para activar.
      Si está ausente o vacío, esta función devuelve `None` y el chat
      sigue funcionando solo con las 5 tools deterministas.
    - `HACIENDA_AI_CORPUS_DIR`: ruta absoluta al directorio raíz del
      corpus (debe contener `normas/`, `jurisprudencia/`, etc.).
      Obligatorio si RAG está activado.
    - `HACIENDA_AI_RAG_COLLECTION`: nombre de la colección Qdrant.
      Default `hacienda_corpus_v1`.
    - `HACIENDA_AI_EMBEDDING_PROVIDER`: `"hash"` (default) o `"voyage"`.
    - `HACIENDA_AI_VECTOR_STORE`: `"memory"` (default) o `"qdrant"`.
    - `HACIENDA_AI_QDRANT_URL`: URL de Qdrant. Obligatorio si
      `store=qdrant`.
    - `HACIENDA_AI_QDRANT_API_KEY`: API key opcional de Qdrant.
    - `VOYAGE_API_KEY`: API key de Voyage. Obligatorio si
      `provider=voyage`. También se acepta
      `HACIENDA_AI_VOYAGE_API_KEY`.
    - `HACIENDA_AI_RAG_AUTO_INDEX`: `"0"` para desactivar indexación
      en arranque (asume corpus preindexado). Default `"1"`.
    """
    source = env if env is not None else os.environ
    if not _flag_enabled(source.get("HACIENDA_AI_RAG_ENABLED")):
        return None

    corpus_dir = source.get("HACIENDA_AI_CORPUS_DIR")
    if not corpus_dir:
        raise RetrieverFactoryError(
            "HACIENDA_AI_RAG_ENABLED está activo pero falta "
            "HACIENDA_AI_CORPUS_DIR (ruta al corpus a indexar)."
        )

    provider_name = (source.get("HACIENDA_AI_EMBEDDING_PROVIDER") or "hash").lower()
    if provider_name not in ("hash", "voyage"):
        raise RetrieverFactoryError(
            f"HACIENDA_AI_EMBEDDING_PROVIDER inválido: {provider_name!r}. "
            "Valores válidos: 'hash', 'voyage'."
        )

    store_name = (source.get("HACIENDA_AI_VECTOR_STORE") or "memory").lower()
    if store_name not in ("memory", "qdrant"):
        raise RetrieverFactoryError(
            f"HACIENDA_AI_VECTOR_STORE inválido: {store_name!r}. "
            "Valores válidos: 'memory', 'qdrant'."
        )

    qdrant_url = source.get("HACIENDA_AI_QDRANT_URL")
    if store_name == "qdrant" and not qdrant_url:
        raise RetrieverFactoryError(
            "HACIENDA_AI_VECTOR_STORE=qdrant requiere "
            "HACIENDA_AI_QDRANT_URL."
        )

    voyage_key = source.get("HACIENDA_AI_VOYAGE_API_KEY") or source.get(
        "VOYAGE_API_KEY"
    )
    if provider_name == "voyage" and not voyage_key:
        raise RetrieverFactoryError(
            "HACIENDA_AI_EMBEDDING_PROVIDER=voyage requiere "
            "VOYAGE_API_KEY (o HACIENDA_AI_VOYAGE_API_KEY)."
        )

    auto_index = _flag_enabled(
        source.get("HACIENDA_AI_RAG_AUTO_INDEX"),
        default=True,
    )

    config = RetrieverConfig(
        data_dir=Path(corpus_dir),
        collection=source.get("HACIENDA_AI_RAG_COLLECTION") or DEFAULT_COLLECTION,
        provider=provider_name,  # type: ignore[arg-type]
        store=store_name,  # type: ignore[arg-type]
        qdrant_url=qdrant_url,
        qdrant_api_key=source.get("HACIENDA_AI_QDRANT_API_KEY"),
        voyage_api_key=voyage_key,
        auto_index=auto_index,
    )
    return build_retriever_from_config(config)


# ---------- Helpers privados ----------


def _build_provider(config: RetrieverConfig) -> EmbeddingProvider:
    if config.provider == "hash":
        return DeterministicHashEmbeddings(dim=config.hash_dim)
    if config.provider == "voyage":
        if not config.voyage_api_key:
            raise RetrieverFactoryError(
                "provider='voyage' requiere voyage_api_key en la config."
            )
        return VoyageEmbeddings(api_key=config.voyage_api_key)
    raise RetrieverFactoryError(f"provider desconocido: {config.provider!r}")


def _build_store(config: RetrieverConfig) -> VectorStore:
    if config.store == "memory":
        return InMemoryVectorStore()
    if config.store == "qdrant":
        if not config.qdrant_url:
            raise RetrieverFactoryError(
                "store='qdrant' requiere qdrant_url en la config."
            )
        return QdrantVectorStore(
            url=config.qdrant_url,
            api_key=config.qdrant_api_key,
        )
    raise RetrieverFactoryError(f"store desconocido: {config.store!r}")


_TRUTHY = frozenset({"1", "true", "yes", "y", "on"})
_FALSY = frozenset({"0", "false", "no", "n", "off", ""})


def _flag_enabled(raw: str | None, *, default: bool = False) -> bool:
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUTHY:
        return True
    if value in _FALSY:
        return False
    raise RetrieverFactoryError(
        f"valor booleano inválido: {raw!r}. Usa '1'/'0', 'true'/'false', 'yes'/'no'."
    )


__all__ = [
    "DEFAULT_COLLECTION",
    "RetrieverBuildReport",
    "RetrieverConfig",
    "RetrieverFactoryError",
    "build_retriever_from_config",
    "build_retriever_from_env",
]
