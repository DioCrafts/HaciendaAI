"""Tests del cargador de corpus a `IndexableChunk`."""

from __future__ import annotations

from pathlib import Path

from hacienda_ai.rag.vector import (
    SourceType,
    iter_corpus_chunks,
    iter_dgt_chunks,
    iter_manual_chunks,
    iter_norma_chunks,
    iter_sentencia_chunks,
    iter_teac_chunks,
)

CORPUS = Path(__file__).parent / "fixtures" / "vector" / "corpus"


def test_iter_norma_chunks_genera_uno_por_version() -> None:
    chunks = list(iter_norma_chunks(CORPUS / "normas"))
    # Fixture: 1 norma con 1 versión → 1 chunk.
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.source_type == SourceType.NORMA
    assert chunk.chunk_id.startswith("norma::BOE-A-2006-20764::v")
    assert "Ley 35/2006" in chunk.text or "IRPF" in chunk.text
    assert chunk.metadata["effective_from"] == "2007-01-01"


def test_iter_sentencia_chunks_genera_uno_por_sentencia() -> None:
    chunks = list(iter_sentencia_chunks(CORPUS / "jurisprudencia"))
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.source_type == SourceType.SENTENCIA
    assert chunk.chunk_id == "sentencia::ECLI:ES:TS:2024:1234"
    assert "TS" in chunk.text
    assert chunk.metadata["organo"] == "ts"
    assert chunk.metadata["fecha"] == "2024-06-15"


def test_iter_dgt_chunks_incluye_criterio() -> None:
    chunks = list(iter_dgt_chunks(CORPUS / "dgt_consultas"))
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.source_type == SourceType.CONSULTA_DGT
    assert chunk.chunk_id == "consulta_dgt::V0123-24"
    # Cuestión planteada va en el texto.
    assert "Cuestión" in chunk.text or "cuestión" in chunk.text.lower()
    assert chunk.metadata["impuesto"] == "irpf"


def test_iter_teac_chunks_metadata_tipo_resolucion() -> None:
    chunks = list(iter_teac_chunks(CORPUS / "teac_resoluciones"))
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk.source_type == SourceType.RESOLUCION_TEAC
    assert chunk.metadata["tipo_resolucion"] == "unifica_criterio"
    assert chunk.metadata["impuesto"] == "irpf"


def test_iter_manual_chunks_propaga_jerarquia() -> None:
    chunks = list(iter_manual_chunks(CORPUS / "manuales"))
    # Fixture: 1 manual IRPF chunk + 1 INFORMA FAQ = 2 chunks.
    assert len(chunks) == 2
    by_id = {c.chunk_id: c for c in chunks}
    manual_id = next(
        cid for cid in by_id if "manual_irpf" in cid and "1_1_1" in cid
    )
    informa_id = next(cid for cid in by_id if "informa_faq" in cid)
    manual = by_id[manual_id]
    informa = by_id[informa_id]

    # Manual: capítulo y subsección presentes.
    assert manual.metadata.get("capitulo")
    assert manual.metadata.get("ejercicio") == 2024
    # INFORMA: sin capítulo, con subseccion (materia).
    assert informa.metadata.get("capitulo") is None
    assert informa.metadata.get("fuente") == "informa_faq"


def test_iter_corpus_chunks_agrega_todas_las_fuentes() -> None:
    chunks = list(iter_corpus_chunks(CORPUS))
    # 1 norma + 1 sentencia + 1 DGT + 1 TEAC + 1 manual + 1 INFORMA = 6.
    assert len(chunks) == 6
    source_types = {c.source_type for c in chunks}
    assert source_types == {
        SourceType.NORMA,
        SourceType.SENTENCIA,
        SourceType.CONSULTA_DGT,
        SourceType.RESOLUCION_TEAC,
        SourceType.MANUAL,
    }


def test_iter_corpus_chunks_directorio_inexistente_no_lanza(tmp_path: Path) -> None:
    """Si una subdir del corpus no existe, el iterador la salta silenciosamente."""
    # tmp_path vacío: ninguna subdir existe.
    chunks = list(iter_corpus_chunks(tmp_path))
    assert chunks == []


def test_chunks_son_indexable_chunk() -> None:
    """Verifica que el contrato `IndexableChunk` se respeta en cada fuente."""
    for chunk in iter_corpus_chunks(CORPUS):
        assert chunk.chunk_id  # no vacío.
        assert chunk.text  # no vacío.
        assert isinstance(chunk.metadata, dict)
        assert isinstance(chunk.source_type, SourceType)


# ---------- Tier / doctrine_weight para filtrado jerárquico ----------


def test_sentencia_chunk_lleva_tier_y_doctrine_weight() -> None:
    """Una sentencia del TS debe llevar tier=TS y doctrine_weight=ISOLATED
    (única sentencia en el fixture, sin compañera para reiterada)."""
    chunks = list(iter_sentencia_chunks(CORPUS / "jurisprudencia"))
    assert len(chunks) == 1
    meta = chunks[0].metadata
    # TS es tier=20 según JurisprudenceTier.
    assert meta["tier"] == 20
    assert meta["tier_label"] == "TS"
    assert meta["doctrine_weight"] == "isolated"


def test_dgt_chunk_lleva_tier_dgt_vinculante() -> None:
    chunks = list(iter_dgt_chunks(CORPUS / "dgt_consultas"))
    assert len(chunks) == 1
    meta = chunks[0].metadata
    # DGT_VINCULANTE = 50.
    assert meta["tier"] == 50
    assert meta["tier_label"] == "DGT_VINCULANTE"
    assert meta["doctrine_weight"] == "isolated"


def test_teac_unifica_chunk_lleva_tier_alto_y_binding() -> None:
    """TEAC unifica criterio (art. 242 LGT) → tier=21 (entre TS y AN) y BINDING."""
    chunks = list(iter_teac_chunks(CORPUS / "teac_resoluciones"))
    assert len(chunks) == 1
    meta = chunks[0].metadata
    assert meta["tier"] == 21
    assert meta["tier_label"] == "TEAC_UNIFICA"
    assert meta["doctrine_weight"] == "binding"
