"""Tests del cableado ingest scripts → vector store.

Verifica que las funciones `index_sentencias`/`index_dgt_consultas`/
`index_teac_resoluciones` indexan correctamente items en el vector
store, generando chunks con la metadata jerárquica (tier + doctrine_weight)
necesaria para que el reranker priorice fuentes vinculantes.
"""

from __future__ import annotations

from datetime import date

from hacienda_ai.models import (
    ConsultaDGT,
    CriterioConfidence,
    FalloSentido,
    Impuesto,
    Organo,
    OrganoTEA,
    RatioConfidence,
    ResolucionTEAC,
    Sentencia,
    SentidoResolucion,
    TipoResolucion,
)
from hacienda_ai.rag.vector import (
    DeterministicHashEmbeddings,
    InMemoryVectorStore,
    SourceType,
    VectorQuery,
    index_dgt_consultas,
    index_sentencias,
    index_teac_resoluciones,
    query_corpus,
)

COLLECTION = "test_collection_v1"


def _provider_and_store() -> tuple[DeterministicHashEmbeddings, InMemoryVectorStore]:
    return DeterministicHashEmbeddings(dim=128), InMemoryVectorStore()


def _make_sentencia(
    ecli: str = "ECLI:ES:TS:2024:1234",
    organo: Organo = Organo.TS,
) -> Sentencia:
    return Sentencia(
        ecli=ecli,
        organo=organo,
        tribunal_codigo="TS" if organo == Organo.TS else "TSJM",
        sala="Tercera",
        seccion=None,
        fecha=date(2024, 6, 15),
        ponente=None,
        numero_resolucion="987/2024",
        numero_recurso=None,
        fallo_sentido=FalloSentido.DESESTIMATORIA,
        fallo_texto="Desestimamos.",
        ratio_decidendi="La carga de la prueba.",
        ratio_confidence=RatioConfidence.AUTO,
        resumen="Dietas IRPF carga prueba",
        url=None,
        content_hash="a" * 64,
        last_fetched_at=date(2024, 9, 1),
    )


def _make_dgt(numero: str = "V0123-24") -> ConsultaDGT:
    return ConsultaDGT(
        numero=numero,
        fecha_salida=date(2024, 1, 30),
        fecha_entrada=None,
        impuesto=Impuesto.IRPF,
        asunto="Dietas IRPF",
        cuestion_planteada="Empleado desplazado.",
        contestacion_completa="Procede.",
        criterio="Las dietas están exoneradas con desplazamiento.",
        criterio_confidence=CriterioConfidence.AUTO,
        normativa=("Ley 35/2006",),
        url=None,
        content_hash="b" * 64,
        last_fetched_at=date(2024, 9, 1),
    )


def _make_teac(
    numero: str = "00/12345/2023",
    tipo: TipoResolucion = TipoResolucion.UNIFICA_CRITERIO,
) -> ResolucionTEAC:
    return ResolucionTEAC(
        numero=numero,
        organo=OrganoTEA.TEAC,
        sede="Madrid",
        fecha=date(2023, 6, 15),
        tipo=tipo,
        sentido=SentidoResolucion.DESESTIMATORIA,
        impuesto=Impuesto.IRPF,
        asunto="Carga prueba dietas",
        criterio="La carga es del pagador.",
        criterio_confidence=CriterioConfidence.AUTO,
        normativa=("Ley 35/2006",),
        resolucion_texto="Texto.",
        url=None,
        content_hash="c" * 64,
        last_fetched_at=date(2024, 9, 1),
    )


# ---------- index_sentencias ----------


def test_index_sentencias_upsertea_y_es_recuperable() -> None:
    provider, store = _provider_and_store()
    report = index_sentencias(
        [_make_sentencia()],
        collection=COLLECTION,
        provider=provider,
        store=store,
    )
    assert report.total_chunks == 1
    assert report.upserted == 1
    assert not report.errors

    # Recuperable: el chunk indexado aparece en una búsqueda.
    matches = query_corpus(
        VectorQuery(text="Dietas IRPF carga prueba", top_k=5),
        collection=COLLECTION,
        provider=provider,
        store=store,
    ).matches
    assert any(
        m.chunk.chunk_id == "sentencia::ECLI:ES:TS:2024:1234"
        for m in matches
    )


def test_index_sentencias_metadata_incluye_tier_y_weight() -> None:
    provider, store = _provider_and_store()
    index_sentencias(
        [_make_sentencia()],
        collection=COLLECTION,
        provider=provider,
        store=store,
    )
    matches = query_corpus(
        VectorQuery(text="dietas", top_k=5),
        collection=COLLECTION,
        provider=provider,
        store=store,
    ).matches
    chunk = next(m.chunk for m in matches if m.chunk.source_type == SourceType.SENTENCIA)
    assert chunk.metadata["tier"] == 20  # TS
    assert chunk.metadata["tier_label"] == "TS"
    # Sentencia única → ISOLATED.
    assert chunk.metadata["doctrine_weight"] == "isolated"
    assert chunk.metadata["ecli"] == "ECLI:ES:TS:2024:1234"


def test_index_sentencias_detecta_reiterada_con_dos_sentencias() -> None:
    provider, store = _provider_and_store()
    s1 = _make_sentencia(ecli="ECLI:ES:TS:2024:1234")
    s2 = _make_sentencia(ecli="ECLI:ES:TS:2024:5678")
    index_sentencias(
        [s1, s2],
        collection=COLLECTION,
        provider=provider,
        store=store,
    )
    matches = query_corpus(
        VectorQuery(text="dietas", top_k=10),
        collection=COLLECTION,
        provider=provider,
        store=store,
    ).matches
    weights = {
        m.chunk.metadata["ecli"]: m.chunk.metadata["doctrine_weight"]
        for m in matches
        if m.chunk.source_type == SourceType.SENTENCIA
    }
    # Mismo asunto, mismo órgano, mismo sentido → CONSOLIDATED.
    assert weights["ECLI:ES:TS:2024:1234"] == "consolidated"
    assert weights["ECLI:ES:TS:2024:5678"] == "consolidated"


# ---------- index_dgt_consultas ----------


def test_index_dgt_upsertea_y_lleva_tier_dgt() -> None:
    provider, store = _provider_and_store()
    report = index_dgt_consultas(
        [_make_dgt()],
        collection=COLLECTION,
        provider=provider,
        store=store,
    )
    assert report.upserted == 1
    matches = query_corpus(
        VectorQuery(text="dietas IRPF", top_k=5),
        collection=COLLECTION,
        provider=provider,
        store=store,
    ).matches
    chunk = next(
        m.chunk for m in matches if m.chunk.source_type == SourceType.CONSULTA_DGT
    )
    assert chunk.chunk_id == "consulta_dgt::V0123-24"
    assert chunk.metadata["tier"] == 50  # DGT_VINCULANTE
    assert chunk.metadata["tier_label"] == "DGT_VINCULANTE"


# ---------- index_teac_resoluciones ----------


def test_index_teac_unifica_es_binding() -> None:
    provider, store = _provider_and_store()
    report = index_teac_resoluciones(
        [_make_teac()],
        collection=COLLECTION,
        provider=provider,
        store=store,
    )
    assert report.upserted == 1
    matches = query_corpus(
        VectorQuery(text="dietas", top_k=5),
        collection=COLLECTION,
        provider=provider,
        store=store,
    ).matches
    chunk = next(
        m.chunk for m in matches if m.chunk.source_type == SourceType.RESOLUCION_TEAC
    )
    assert chunk.metadata["tier"] == 21  # TEAC_UNIFICA
    assert chunk.metadata["tier_label"] == "TEAC_UNIFICA"
    assert chunk.metadata["doctrine_weight"] == "binding"


def test_index_teac_ordinaria_es_isolated() -> None:
    provider, store = _provider_and_store()
    index_teac_resoluciones(
        [_make_teac(numero="00/00001/2023", tipo=TipoResolucion.ORDINARIA)],
        collection=COLLECTION,
        provider=provider,
        store=store,
    )
    matches = query_corpus(
        VectorQuery(text="dietas", top_k=5),
        collection=COLLECTION,
        provider=provider,
        store=store,
    ).matches
    chunk = next(
        m.chunk for m in matches if m.chunk.source_type == SourceType.RESOLUCION_TEAC
    )
    assert chunk.metadata["tier"] == 41  # TEAC_ORDINARIA
    assert chunk.metadata["doctrine_weight"] == "isolated"


# ---------- Idempotencia ----------


def test_reindex_misma_sentencia_no_duplica() -> None:
    """`chunk_id = sentencia::ECLI` es estable: upsert sobre la misma
    sentencia debe sobreescribir, no duplicar."""
    provider, store = _provider_and_store()
    senten = _make_sentencia()
    index_sentencias([senten], collection=COLLECTION, provider=provider, store=store)
    index_sentencias([senten], collection=COLLECTION, provider=provider, store=store)
    matches = query_corpus(
        VectorQuery(text="dietas", top_k=10),
        collection=COLLECTION,
        provider=provider,
        store=store,
    ).matches
    senten_chunks = [
        m for m in matches if m.chunk.source_type == SourceType.SENTENCIA
    ]
    assert len(senten_chunks) == 1
