"""Indexa el corpus en un vector store (Qdrant o InMemory) usando Voyage o hash.

Modos:

    # Demo / CI: hash determinista + InMemory (no requiere servicios externos).
    python scripts/index_vector_store.py \\
        --provider hash --store memory --dump out.json

    # Producción: voyage-law-2 + Qdrant self-hosted.
    export VOYAGE_API_KEY=...
    python scripts/index_vector_store.py \\
        --provider voyage --store qdrant \\
        --qdrant-url http://localhost:6333 \\
        --collection hacienda_v1

    # Solo una fuente del corpus:
    python scripts/index_vector_store.py \\
        --provider hash --store memory \\
        --only manuales --dump out.json

Códigos de salida:
    0 — indexado correcto.
    1 — errores parciales en batches (algunos chunks no se embebieron).
    2 — error fatal (config inválida, store inalcanzable).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from hacienda_ai.rag.vector import (  # noqa: E402
    DeterministicHashEmbeddings,
    InMemoryVectorStore,
    QdrantVectorStore,
    VoyageEmbeddings,
    index_corpus,
    iter_corpus_chunks,
    iter_dgt_chunks,
    iter_manual_chunks,
    iter_norma_chunks,
    iter_sentencia_chunks,
    iter_teac_chunks,
)
from hacienda_ai.rag.vector.embedded_chunk import IndexableChunk  # noqa: E402
from hacienda_ai.rag.vector.provider import EmbeddingProvider  # noqa: E402
from hacienda_ai.rag.vector.store import VectorStore  # noqa: E402

DEFAULT_DATA_DIR = REPO_ROOT / "src" / "hacienda_ai" / "data"
DEFAULT_COLLECTION = "hacienda_corpus_v1"


def _build_provider(args: argparse.Namespace) -> EmbeddingProvider:
    if args.provider == "hash":
        return DeterministicHashEmbeddings(dim=args.hash_dim)
    if args.provider == "voyage":
        return VoyageEmbeddings(api_key=args.voyage_api_key)
    raise ValueError(f"provider desconocido: {args.provider}")


def _build_store(args: argparse.Namespace) -> VectorStore:
    if args.store == "memory":
        return InMemoryVectorStore()
    if args.store == "qdrant":
        return QdrantVectorStore(
            url=args.qdrant_url,
            api_key=args.qdrant_api_key,
        )
    raise ValueError(f"store desconocido: {args.store}")


def _build_chunks_iter(args: argparse.Namespace):
    if args.only == "all":
        return iter_corpus_chunks(args.data_dir)
    sub = args.data_dir
    mapping = {
        "normas": (iter_norma_chunks, sub / "normas"),
        "jurisprudencia": (iter_sentencia_chunks, sub / "jurisprudencia"),
        "dgt": (iter_dgt_chunks, sub / "dgt_consultas"),
        "teac": (iter_teac_chunks, sub / "teac_resoluciones"),
        "manuales": (iter_manual_chunks, sub / "manuales"),
    }
    fn, path = mapping[args.only]
    return fn(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Indexa el corpus en un vector store.")
    parser.add_argument(
        "--provider",
        choices=("hash", "voyage"),
        default="hash",
        help="Proveedor de embeddings.",
    )
    parser.add_argument("--hash-dim", type=int, default=1024)
    parser.add_argument(
        "--voyage-api-key",
        default=None,
        help="API key Voyage (alternativa a VOYAGE_API_KEY).",
    )
    parser.add_argument(
        "--store",
        choices=("memory", "qdrant"),
        default="memory",
        help="Backend del vector store.",
    )
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--qdrant-api-key", default=None)
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Raíz `data/` desde donde leer el corpus.",
    )
    parser.add_argument(
        "--only",
        choices=("all", "normas", "jurisprudencia", "dgt", "teac", "manuales"),
        default="all",
    )
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument(
        "--dump",
        type=Path,
        default=None,
        help="Si --store=memory, dumpea el contenido a este JSON (debug).",
    )
    args = parser.parse_args(argv)

    try:
        provider = _build_provider(args)
        store = _build_store(args)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR construyendo provider/store: {exc}", file=sys.stderr)
        return 2

    chunks_iter = _build_chunks_iter(args)
    # Materializamos para tener el conteo total previo a indexar
    # (con el iterador perezoso solo veríamos el total al final).
    chunks: list[IndexableChunk] = list(chunks_iter)
    print(
        f"Indexando {len(chunks)} chunks con provider={args.provider} "
        f"(dim={provider.dim}, model={provider.model_id}) "
        f"en store={args.store} colección {args.collection!r}..."
    )

    try:
        report = index_corpus(
            chunks,
            collection=args.collection,
            provider=provider,
            store=store,
            batch_size=args.batch_size,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR fatal indexando: {exc}", file=sys.stderr)
        return 2

    print(
        f"Resumen: total={report.total_chunks} "
        f"upserted={report.upserted} "
        f"errores={len(report.errors)}"
    )
    for err in report.errors[:10]:
        print(f"  ✗ {err}", file=sys.stderr)

    if args.dump is not None and isinstance(store, InMemoryVectorStore):
        # Debug dump del estado del store en memoria. Solo aplica al
        # backend in-memory; en Qdrant el dump es el propio servicio.
        col_count = store.count(args.collection)
        args.dump.parent.mkdir(parents=True, exist_ok=True)
        args.dump.write_text(
            json.dumps(
                {
                    "collection": args.collection,
                    "count": col_count,
                    "provider_model": provider.model_id,
                    "provider_dim": provider.dim,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"Dump escrito en {args.dump}")

    if report.errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
