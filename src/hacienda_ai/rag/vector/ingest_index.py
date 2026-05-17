"""Pegamento entre los runners de ingesta (DGT/TEAC/CENDOJ) y el indexador.

El patrón "ingerir → indexar" es repetitivo: el ingest script persiste a
disco los modelos del dominio (sentencias, consultas DGT, resoluciones
TEAC) y, si el operador lo pide con `--index`, los upsertea en el vector
store para que el retriever híbrido los encuentre. Este módulo expone
los wrappers necesarios:

- `build_vector_store_args`: añade la familia común de flags CLI
  (`--index`, `--provider`, `--store`, `--qdrant-url`, …) al `ArgumentParser`
  del ingest script. Mantiene la nomenclatura idéntica a
  `scripts/index_vector_store.py` para no exigir al operador aprender
  flags distintos en cada script.

- `index_sentencias` / `index_dgt_consultas` / `index_teac_resoluciones`:
  toman la lista de items recién aceptados por el runner y los indexan
  en una colección, devolviendo el `IndexReport`. La conversión a
  `IndexableChunk` reutiliza las mismas funciones que `corpus.py` para
  que los chunks indexados aquí sean indistinguibles de los indexados
  por la pipeline batch.

Diseño:

- **Opt-in**: si el operador no pasa `--index`, los runners persisten a
  disco como siempre. La retrocompatibilidad es total.
- **Idempotente**: el `chunk_id` está derivado del identificador
  canónico (`sentencia::ECLI`, `consulta_dgt::V0123-24`,
  `resolucion_teac::00/12345/2023`), así que reindexar el mismo item
  es un upsert que sobreescribe sin duplicar.
- **No bloquea persistencia**: errores de indexación se acumulan en el
  `IndexReport` pero no abortan el script — la persistencia a JSON
  sigue siendo la fuente canónica. Si el indexado falla, el operador
  puede reintentarlo después con `scripts/index_vector_store.py`.
"""

from __future__ import annotations

import argparse
from typing import Any, Iterable

from ...models import ConsultaDGT, ResolucionTEAC, Sentencia
from ...safety.jurisprudence_registry import (
    DoctrineWeight,
    JurisprudenceTier,
    compute_sentencia_weights,
    compute_teac_weights,
    tier_for_sentencia,
    tier_for_teac,
)
from .embedded_chunk import IndexableChunk, SourceType
from .memory import InMemoryVectorStore
from .provider import DeterministicHashEmbeddings, EmbeddingProvider
from .qdrant import QdrantVectorStore
from .runner import IndexReport, index_corpus
from .store import VectorStore
from .voyage import VoyageEmbeddings

DEFAULT_COLLECTION = "hacienda_corpus_v1"


class IngestIndexConfigError(RuntimeError):
    """Configuración CLI inválida para indexación post-ingesta."""


def build_vector_store_args(parser: argparse.ArgumentParser) -> None:
    """Añade la familia común de flags CLI al parser.

    El bloque se nombra "Indexación" en `--help` para que el operador
    distinga claramente los flags del runner principal (que persisten a
    JSON) de los flags de la fase opcional de indexación.
    """
    group = parser.add_argument_group("Indexación (opt-in)")
    group.add_argument(
        "--index",
        action="store_true",
        help=(
            "Tras persistir a JSON, embebe e indexa los items aceptados "
            "en el vector store configurado. Por defecto no se indexa "
            "y el operador debe lanzar scripts/index_vector_store.py por "
            "separado."
        ),
    )
    group.add_argument(
        "--provider",
        choices=("hash", "voyage"),
        default="hash",
        help=(
            "Proveedor de embeddings. 'hash' es determinista y sin red "
            "(útil para CI y demos); 'voyage' usa voyage-law-2 contra "
            "api.voyageai.com (requiere VOYAGE_API_KEY)."
        ),
    )
    group.add_argument(
        "--voyage-api-key",
        default=None,
        help="API key Voyage (alternativa a la variable VOYAGE_API_KEY).",
    )
    group.add_argument(
        "--hash-dim",
        type=int,
        default=1024,
        help=(
            "Dimensión del provider 'hash' (default 1024, coincide con "
            "voyage-law-2 para que las colecciones sean intercambiables "
            "entre proveedor en CI vs producción)."
        ),
    )
    group.add_argument(
        "--store",
        choices=("memory", "qdrant"),
        default="memory",
        help="Backend del vector store. 'qdrant' requiere --qdrant-url.",
    )
    group.add_argument(
        "--qdrant-url",
        default=None,
        help="URL del servicio Qdrant (e.g. http://localhost:6333).",
    )
    group.add_argument(
        "--qdrant-api-key",
        default=None,
        help="API key de Qdrant (opcional).",
    )
    group.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help=(
            f"Nombre de la colección destino (default '{DEFAULT_COLLECTION}'). "
            "Debe coincidir con la colección que use el retriever en /chat."
        ),
    )
    group.add_argument(
        "--index-batch-size",
        type=int,
        default=32,
        help="Tamaño del batch al embeber (default 32).",
    )


def build_provider_from_args(args: argparse.Namespace) -> EmbeddingProvider:
    """Construye el `EmbeddingProvider` según los flags CLI."""
    if args.provider == "hash":
        return DeterministicHashEmbeddings(dim=args.hash_dim)
    if args.provider == "voyage":
        if not args.voyage_api_key:
            raise IngestIndexConfigError(
                "--provider=voyage requiere --voyage-api-key (o VOYAGE_API_KEY)."
            )
        return VoyageEmbeddings(api_key=args.voyage_api_key)
    raise IngestIndexConfigError(f"provider desconocido: {args.provider}")


def build_store_from_args(args: argparse.Namespace) -> VectorStore:
    """Construye el `VectorStore` según los flags CLI."""
    if args.store == "memory":
        return InMemoryVectorStore()
    if args.store == "qdrant":
        if not args.qdrant_url:
            raise IngestIndexConfigError(
                "--store=qdrant requiere --qdrant-url."
            )
        return QdrantVectorStore(
            url=args.qdrant_url,
            api_key=args.qdrant_api_key,
        )
    raise IngestIndexConfigError(f"store desconocido: {args.store}")


# ---------- Conversión Sentencia/DGT/TEAC → IndexableChunk ----------


def _sentencias_to_chunks(
    sentencias: list[Sentencia],
) -> list[IndexableChunk]:
    """Replica la lógica de `iter_sentencia_chunks` sobre objetos en memoria.

    Mantener dos rutas (disco vs memoria) acaba en drift; la duplicación
    es deliberadamente mínima para que cualquier ajuste se haga aquí y
    en `corpus.py` simultáneamente. Si crece más, vale la pena extraer
    a una función de bajo nivel `_make_sentencia_chunk(sentencia, weight)`.
    """
    weight_by_ecli = compute_sentencia_weights(sentencias)
    out: list[IndexableChunk] = []
    for sentencia in sentencias:
        text_parts = [
            f"{sentencia.tribunal_codigo} {sentencia.numero_resolucion or ''} "
            f"({sentencia.fecha.isoformat()})",
        ]
        if sentencia.resumen:
            text_parts.append(sentencia.resumen)
        if sentencia.ratio_decidendi:
            text_parts.append(f"Ratio: {sentencia.ratio_decidendi}")
        text_parts.append(f"Fallo: {sentencia.fallo_texto}")

        tier = tier_for_sentencia(sentencia)
        weight = weight_by_ecli.get(sentencia.ecli, DoctrineWeight.ISOLATED)
        metadata: dict[str, Any] = {
            "ecli": sentencia.ecli,
            "organo": sentencia.organo.value,
            "tribunal_codigo": sentencia.tribunal_codigo,
            "fecha": sentencia.fecha.isoformat(),
            "fallo_sentido": sentencia.fallo_sentido.value,
            "ratio_confidence": sentencia.ratio_confidence.value,
            "tier": int(tier),
            "tier_label": tier.name,
            "doctrine_weight": weight.value,
        }
        if sentencia.sala:
            metadata["sala"] = sentencia.sala
        if sentencia.seccion:
            metadata["seccion"] = sentencia.seccion
        out.append(
            IndexableChunk(
                chunk_id=f"sentencia::{sentencia.ecli}",
                source_type=SourceType.SENTENCIA,
                text="\n\n".join(text_parts),
                metadata=metadata,
            )
        )
    return out


def _dgt_to_chunks(consultas: list[ConsultaDGT]) -> list[IndexableChunk]:
    out: list[IndexableChunk] = []
    for consulta in consultas:
        text_parts = [
            f"DGT {consulta.numero} ({consulta.fecha_salida.isoformat()}) — "
            f"{consulta.asunto}",
            f"Cuestión: {consulta.cuestion_planteada[:1500]}",
        ]
        if consulta.criterio:
            text_parts.append(f"Criterio: {consulta.criterio}")
        else:
            text_parts.append(
                f"Contestación: {consulta.contestacion_completa[:1500]}"
            )
        metadata: dict[str, Any] = {
            "numero": consulta.numero,
            "impuesto": consulta.impuesto.value,
            "fecha": consulta.fecha_salida.isoformat(),
            "criterio_confidence": consulta.criterio_confidence.value,
            "tier": int(JurisprudenceTier.DGT_VINCULANTE),
            "tier_label": JurisprudenceTier.DGT_VINCULANTE.name,
            "doctrine_weight": DoctrineWeight.ISOLATED.value,
        }
        if consulta.normativa:
            metadata["normativa"] = list(consulta.normativa)
        out.append(
            IndexableChunk(
                chunk_id=f"consulta_dgt::{consulta.numero}",
                source_type=SourceType.CONSULTA_DGT,
                text="\n\n".join(text_parts),
                metadata=metadata,
            )
        )
    return out


def _teac_to_chunks(
    resoluciones: list[ResolucionTEAC],
) -> list[IndexableChunk]:
    weight_by_numero = compute_teac_weights(resoluciones)
    out: list[IndexableChunk] = []
    for resolucion in resoluciones:
        text_parts = [
            f"{resolucion.organo.value.upper()} {resolucion.numero} "
            f"({resolucion.fecha.isoformat()}, {resolucion.tipo.value}) — "
            f"{resolucion.asunto}",
        ]
        if resolucion.criterio:
            text_parts.append(f"Criterio: {resolucion.criterio}")
        tier = tier_for_teac(resolucion)
        weight = weight_by_numero.get(
            resolucion.numero, DoctrineWeight.ISOLATED
        )
        metadata: dict[str, Any] = {
            "numero": resolucion.numero,
            "organo": resolucion.organo.value,
            "tipo_resolucion": resolucion.tipo.value,
            "sentido": resolucion.sentido.value,
            "impuesto": resolucion.impuesto.value,
            "fecha": resolucion.fecha.isoformat(),
            "criterio_confidence": resolucion.criterio_confidence.value,
            "tier": int(tier),
            "tier_label": tier.name,
            "doctrine_weight": weight.value,
        }
        if resolucion.sede:
            metadata["sede"] = resolucion.sede
        if resolucion.normativa:
            metadata["normativa"] = list(resolucion.normativa)
        out.append(
            IndexableChunk(
                chunk_id=f"resolucion_teac::{resolucion.numero}",
                source_type=SourceType.RESOLUCION_TEAC,
                text="\n\n".join(text_parts),
                metadata=metadata,
            )
        )
    return out


# ---------- Funciones públicas de indexación post-ingesta ----------


def index_sentencias(
    sentencias: Iterable[Sentencia],
    *,
    collection: str,
    provider: EmbeddingProvider,
    store: VectorStore,
    batch_size: int = 32,
) -> IndexReport:
    """Indexa una lista de sentencias en el vector store dado."""
    chunks = _sentencias_to_chunks(list(sentencias))
    return index_corpus(
        chunks,
        collection=collection,
        provider=provider,
        store=store,
        batch_size=batch_size,
    )


def index_dgt_consultas(
    consultas: Iterable[ConsultaDGT],
    *,
    collection: str,
    provider: EmbeddingProvider,
    store: VectorStore,
    batch_size: int = 32,
) -> IndexReport:
    """Indexa consultas DGT vinculantes en el vector store dado."""
    chunks = _dgt_to_chunks(list(consultas))
    return index_corpus(
        chunks,
        collection=collection,
        provider=provider,
        store=store,
        batch_size=batch_size,
    )


def index_teac_resoluciones(
    resoluciones: Iterable[ResolucionTEAC],
    *,
    collection: str,
    provider: EmbeddingProvider,
    store: VectorStore,
    batch_size: int = 32,
) -> IndexReport:
    """Indexa resoluciones TEAC/TEAR en el vector store dado."""
    chunks = _teac_to_chunks(list(resoluciones))
    return index_corpus(
        chunks,
        collection=collection,
        provider=provider,
        store=store,
        batch_size=batch_size,
    )


__all__ = [
    "DEFAULT_COLLECTION",
    "IngestIndexConfigError",
    "build_provider_from_args",
    "build_store_from_args",
    "build_vector_store_args",
    "index_dgt_consultas",
    "index_sentencias",
    "index_teac_resoluciones",
]
