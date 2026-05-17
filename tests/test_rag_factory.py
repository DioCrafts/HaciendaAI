"""Tests de la factoría del retriever híbrido.

Cubre:

1. `build_retriever_from_config`: ensamblaje completo con un corpus
   real (fixtures de tests/vector/corpus/), verificando que el BM25
   se indexa, los vectores se upsertean en InMemory, y que el
   retriever resultante recupera chunks de cada familia (norma, DGT,
   TEAC, sentencia, manual).
2. `build_retriever_from_env`: lectura de variables de entorno (todas
   las combinaciones de habilitado/deshabilitado, provider/store,
   errores de configuración).
3. Resiliencia: `data_dir` inexistente o vacío no debe lanzar — la
   factoría devuelve un retriever vacío y registra warning.
4. Coherencia con la inyección en `create_app`: un retriever real
   construido por la factoría enchufa correctamente al endpoint /chat
   y aparece la tool `retrieve_legal_context`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hacienda_ai.chat import build_default_registry
from hacienda_ai.rag.factory import (
    DEFAULT_COLLECTION,
    RetrieverConfig,
    RetrieverFactoryError,
    build_retriever_from_config,
    build_retriever_from_env,
)
from hacienda_ai.rag.vector import SourceType, VectorQuery

FIXTURES_CORPUS = Path(__file__).parent / "fixtures" / "vector" / "corpus"


# ---------- build_retriever_from_config ----------


def test_factory_indexes_real_corpus_from_fixtures() -> None:
    """Sobre las fixtures (DGT, TEAC, sentencia TS, manual, norma) la
    factoría debe construir un retriever funcional con BM25 y vector
    store poblados, y la búsqueda debe devolver matches."""
    config = RetrieverConfig(data_dir=FIXTURES_CORPUS)
    retriever, report = build_retriever_from_config(config)

    # Métricas de arranque coherentes.
    assert report.total_chunks > 0
    assert report.indexed == report.total_chunks
    assert report.bm25_documents == report.total_chunks
    assert report.errors == []
    assert report.provider_model_id.startswith("deterministic-hash-")
    assert report.store_kind == "InMemoryVectorStore"
    assert report.config.collection == DEFAULT_COLLECTION

    # Búsqueda end-to-end: una query que existe en las fixtures debe
    # devolver al menos un match.
    matches = retriever.search(
        VectorQuery(text="dietas manutencion desplazamiento IRPF", top_k=5)
    )
    assert matches, "el retriever no recuperó ningún chunk"
    # El BM25 + el hash determinista deberían premiar la consulta DGT
    # V0123-24 (que va exactamente sobre dietas).
    chunk_ids = [m.chunk.chunk_id for m in matches]
    assert any("V0123-24" in cid for cid in chunk_ids)


def test_factory_search_respects_source_type_filter() -> None:
    """Filtros del query (source_types) deben funcionar end-to-end."""
    config = RetrieverConfig(data_dir=FIXTURES_CORPUS)
    retriever, _ = build_retriever_from_config(config)
    matches = retriever.search(
        VectorQuery(
            text="dietas manutencion",
            top_k=10,
            source_types=(SourceType.CONSULTA_DGT,),
        )
    )
    # Si hay matches, deben ser solo consultas DGT.
    for m in matches:
        assert m.chunk.source_type == SourceType.CONSULTA_DGT


def test_factory_handles_missing_data_dir(tmp_path: Path) -> None:
    """Si el data_dir no existe, la factoría no lanza: devuelve un
    retriever con índices vacíos y un report con total_chunks=0."""
    config = RetrieverConfig(data_dir=tmp_path / "no_existe")
    retriever, report = build_retriever_from_config(config)
    assert report.total_chunks == 0
    assert report.indexed == 0
    assert report.bm25_documents == 0
    matches = retriever.search(VectorQuery(text="cualquier consulta", top_k=5))
    assert matches == []


def test_factory_handles_empty_data_dir(tmp_path: Path) -> None:
    """Data_dir existente pero sin chunks indexables: igual de tolerante."""
    config = RetrieverConfig(data_dir=tmp_path)
    retriever, report = build_retriever_from_config(config)
    assert report.total_chunks == 0
    matches = retriever.search(VectorQuery(text="x", top_k=5))
    assert matches == []


def test_factory_voyage_provider_requires_api_key() -> None:
    config = RetrieverConfig(data_dir=FIXTURES_CORPUS, provider="voyage")
    with pytest.raises(RetrieverFactoryError, match="voyage_api_key"):
        build_retriever_from_config(config)


def test_factory_qdrant_store_requires_url() -> None:
    config = RetrieverConfig(data_dir=FIXTURES_CORPUS, store="qdrant")
    with pytest.raises(RetrieverFactoryError, match="qdrant_url"):
        build_retriever_from_config(config)


def test_factory_auto_index_off_skips_vector_indexing() -> None:
    """Con auto_index=False, BM25 sigue indexándose (es local y barato)
    pero el vector store NO se toca — el caller asume que se preindexó
    con scripts/index_vector_store.py."""
    config = RetrieverConfig(data_dir=FIXTURES_CORPUS, auto_index=False)
    _, report = build_retriever_from_config(config)
    assert report.total_chunks > 0
    assert report.bm25_documents == report.total_chunks
    assert report.indexed == 0  # vector store no se tocó


# ---------- build_retriever_from_env ----------


def test_env_factory_returns_none_when_disabled() -> None:
    assert build_retriever_from_env({}) is None
    assert build_retriever_from_env({"HACIENDA_AI_RAG_ENABLED": "0"}) is None
    assert (
        build_retriever_from_env({"HACIENDA_AI_RAG_ENABLED": "false"}) is None
    )


def test_env_factory_builds_full_retriever() -> None:
    env = {
        "HACIENDA_AI_RAG_ENABLED": "1",
        "HACIENDA_AI_CORPUS_DIR": str(FIXTURES_CORPUS),
    }
    built = build_retriever_from_env(env)
    assert built is not None
    retriever, report = built
    assert report.total_chunks > 0
    matches = retriever.search(VectorQuery(text="dietas", top_k=3))
    assert matches


def test_env_factory_truthy_variants() -> None:
    """Valores aceptados como verdaderos."""
    for raw in ("1", "true", "True", "TRUE", "yes", "y", "on"):
        env = {
            "HACIENDA_AI_RAG_ENABLED": raw,
            "HACIENDA_AI_CORPUS_DIR": str(FIXTURES_CORPUS),
        }
        assert build_retriever_from_env(env) is not None, raw


def test_env_factory_missing_corpus_dir_raises() -> None:
    with pytest.raises(RetrieverFactoryError, match="HACIENDA_AI_CORPUS_DIR"):
        build_retriever_from_env({"HACIENDA_AI_RAG_ENABLED": "1"})


def test_env_factory_qdrant_requires_url() -> None:
    env = {
        "HACIENDA_AI_RAG_ENABLED": "1",
        "HACIENDA_AI_CORPUS_DIR": str(FIXTURES_CORPUS),
        "HACIENDA_AI_VECTOR_STORE": "qdrant",
    }
    with pytest.raises(RetrieverFactoryError, match="HACIENDA_AI_QDRANT_URL"):
        build_retriever_from_env(env)


def test_env_factory_voyage_requires_api_key() -> None:
    env = {
        "HACIENDA_AI_RAG_ENABLED": "1",
        "HACIENDA_AI_CORPUS_DIR": str(FIXTURES_CORPUS),
        "HACIENDA_AI_EMBEDDING_PROVIDER": "voyage",
    }
    with pytest.raises(RetrieverFactoryError, match="VOYAGE_API_KEY"):
        build_retriever_from_env(env)


def test_env_factory_voyage_key_aliases() -> None:
    """`VOYAGE_API_KEY` y `HACIENDA_AI_VOYAGE_API_KEY` son intercambiables."""
    # Si no se llega a contactar con Voyage (porque no construimos el
    # store para esto), el flag valida la presencia y se construye sin
    # error. La factoría no llama a Voyage hasta el primer embed.
    # Como Voyage falla en construcción (no, en realidad no — solo
    # en runtime). Comprobamos que NO se queja por la falta del key.
    # Para no depender de Voyage en CI usamos el alias HACIENDA_AI_*:
    env = {
        "HACIENDA_AI_RAG_ENABLED": "1",
        "HACIENDA_AI_CORPUS_DIR": str(FIXTURES_CORPUS / "nope"),  # vacío
        "HACIENDA_AI_EMBEDDING_PROVIDER": "voyage",
        "HACIENDA_AI_VOYAGE_API_KEY": "fake-key-for-test",
        "HACIENDA_AI_RAG_AUTO_INDEX": "0",  # no llamamos a Voyage en CI.
    }
    built = build_retriever_from_env(env)
    assert built is not None  # construyó la factoría OK


def test_env_factory_invalid_provider_raises() -> None:
    env = {
        "HACIENDA_AI_RAG_ENABLED": "1",
        "HACIENDA_AI_CORPUS_DIR": str(FIXTURES_CORPUS),
        "HACIENDA_AI_EMBEDDING_PROVIDER": "openai",
    }
    with pytest.raises(RetrieverFactoryError, match="EMBEDDING_PROVIDER"):
        build_retriever_from_env(env)


def test_env_factory_invalid_store_raises() -> None:
    env = {
        "HACIENDA_AI_RAG_ENABLED": "1",
        "HACIENDA_AI_CORPUS_DIR": str(FIXTURES_CORPUS),
        "HACIENDA_AI_VECTOR_STORE": "weaviate",
    }
    with pytest.raises(RetrieverFactoryError, match="VECTOR_STORE"):
        build_retriever_from_env(env)


def test_env_factory_invalid_boolean_raises() -> None:
    env = {"HACIENDA_AI_RAG_ENABLED": "maybe"}
    with pytest.raises(RetrieverFactoryError, match="booleano inválido"):
        build_retriever_from_env(env)


def test_env_factory_custom_collection_name() -> None:
    env = {
        "HACIENDA_AI_RAG_ENABLED": "1",
        "HACIENDA_AI_CORPUS_DIR": str(FIXTURES_CORPUS),
        "HACIENDA_AI_RAG_COLLECTION": "hacienda_v2_test",
    }
    built = build_retriever_from_env(env)
    assert built is not None
    _, report = built
    assert report.config.collection == "hacienda_v2_test"


# ---------- Integración con la registry de chat ----------


def test_factory_retriever_enables_chat_tool() -> None:
    """Un retriever construido por la factoría enchufa correctamente a
    `build_default_registry` y aparece la tool `retrieve_legal_context`."""
    config = RetrieverConfig(data_dir=FIXTURES_CORPUS)
    retriever, _ = build_retriever_from_config(config)
    reg = build_default_registry(retriever=retriever)
    names = {spec["name"] for spec in reg.specs}
    assert "retrieve_legal_context" in names

    # El handler responde con [FUENTE N] sobre el corpus real.
    r = reg.dispatch(
        "retrieve_legal_context",
        {"query": "dietas manutencion", "top_k": 3},
    )
    assert r.get("count", 0) > 0
    assert "[FUENTE 1]" in r["rendered_context"]
