"""Tests del citation grounding: contexto LLM + validador de citas."""

from __future__ import annotations

from datetime import date

from hacienda_ai.rag.grounding import (
    GroundingVerdictLevel,
    build_llm_context,
    format_metadata_for_llm,
    validate_grounded_response,
)
from hacienda_ai.rag.temporal import (
    TemporalEnforcementMode,
    enforce_temporal_filter,
)
from hacienda_ai.rag.vector import (
    EmbeddedChunk,
    SourceType,
    VectorMatch,
    VectorQuery,
)


def _norma_match() -> VectorMatch:
    return VectorMatch(
        chunk=EmbeddedChunk(
            chunk_id="norma::BOE-A-2006-20764::a19::ap2_e::v2015-01-01",
            source_type=SourceType.NORMA,
            text=(
                "Los gastos de defensa jurídica derivados directamente de "
                "litigios suscitados en la relación del contribuyente con "
                "la persona de la que percibe los rendimientos, con el "
                "límite de 300 euros anuales."
            ),
            embedding=(0.1,),
            embedding_model="test",
            metadata={
                "boe_id": "BOE-A-2006-20764",
                "kind": "ley",
                "articulo": "art. 19",
                "apartado": "2.e)",
                "effective_from": "2015-01-01",
                "vigencia_desde": "2015-01-01",
                "jerarquia": ["TÍTULO III", "CAPÍTULO I", "Sección 1ª"],
                "impuesto": "irpf",
            },
        ),
        score=0.95,
    )


def _dgt_match() -> VectorMatch:
    return VectorMatch(
        chunk=EmbeddedChunk(
            chunk_id="consulta_dgt::V0123-24",
            source_type=SourceType.CONSULTA_DGT,
            text=(
                "Consulta sobre gastos de defensa jurídica en procedimiento "
                "tributario. Esta DG considera que no son deducibles."
            ),
            embedding=(0.1,),
            embedding_model="test",
            metadata={
                "numero": "V0123-24",
                "fecha": "2024-01-30",
                "impuesto": "irpf",
            },
        ),
        score=0.85,
    )


def _sentencia_match() -> VectorMatch:
    return VectorMatch(
        chunk=EmbeddedChunk(
            chunk_id="sentencia::ECLI:ES:TS:2024:1234",
            source_type=SourceType.SENTENCIA,
            text=(
                "ECLI:ES:TS:2024:1234. Sala 3ª Sec.2ª. Desestimar el "
                "recurso. Doctrina: los gastos no son deducibles."
            ),
            embedding=(0.1,),
            embedding_model="test",
            metadata={
                "ecli": "ECLI:ES:TS:2024:1234",
                "tribunal_codigo": "TS",
                "organo": "ts",
                "fecha": "2024-06-15",
                "fallo_sentido": "desestimatoria",
            },
        ),
        score=0.80,
    )


# ---------- format_metadata_for_llm ----------


def test_format_metadata_incluye_boe_id_y_pinpoint() -> None:
    meta = {
        "boe_id": "BOE-A-2006-20764",
        "articulo": "art. 19",
        "apartado": "2.e)",
        "vigencia_desde": "2015-01-01",
    }
    lines = format_metadata_for_llm(meta)
    joined = "\n".join(lines)
    assert "BOE-A-2006-20764" in joined
    assert "art. 19" in joined
    assert "2.e)" in joined
    assert "2015-01-01" in joined


def test_format_metadata_incluye_jerarquia() -> None:
    meta = {"jerarquia": ["TÍTULO III", "CAPÍTULO I"]}
    lines = format_metadata_for_llm(meta)
    assert any("TÍTULO III > CAPÍTULO I" in line for line in lines)


def test_format_metadata_vacia_devuelve_tupla_vacia() -> None:
    assert format_metadata_for_llm({}) == ()


# ---------- build_llm_context ----------


def test_build_context_numera_fuentes_uno_indexed() -> None:
    context = build_llm_context([_norma_match(), _dgt_match()])
    assert len(context.sources) == 2
    assert context.sources[0].index == 1
    assert context.sources[1].index == 2
    assert context.source_ids_by_index[1] == _norma_match().chunk.chunk_id
    assert context.source_ids_by_index[2] == _dgt_match().chunk.chunk_id


def test_build_context_render_contiene_FUENTE_y_metadata() -> None:
    context = build_llm_context([_norma_match()])
    assert "[FUENTE 1]" in context.rendered
    assert "BOE-A-2006-20764" in context.rendered
    assert "art. 19" in context.rendered
    assert "defensa jurídica" in context.rendered


def test_build_context_trunca_a_max_sources() -> None:
    matches = [_norma_match()] * 20
    context = build_llm_context(matches, max_sources=5)
    assert len(context.sources) == 5


def test_build_context_lista_vacia() -> None:
    context = build_llm_context([])
    assert context.sources == ()
    assert context.rendered == ""


def test_build_context_header_distingue_tipo_fuente() -> None:
    context = build_llm_context(
        [_norma_match(), _dgt_match(), _sentencia_match()]
    )
    headers = [s.header for s in context.sources]
    assert any("Norma" in h for h in headers)
    assert any("Consulta DGT" in h for h in headers)
    assert any("Sentencia TS" in h for h in headers)


# ---------- validate_grounded_response ----------


def test_validate_safe_si_cita_FUENTE_existente_y_cita_normativa_en_contexto() -> None:
    context = build_llm_context([_norma_match()])
    response = (
        "Según el art. 19 LIRPF (BOE-A-2006-20764), los gastos de "
        "defensa jurídica son deducibles solo si derivan directamente "
        "de la relación laboral [FUENTE 1]."
    )
    verdict = validate_grounded_response(response, context=context)
    # FUENTE 1 existe y BOE-A-2006-20764 aparece en metadata del chunk.
    assert verdict.level == GroundingVerdictLevel.SAFE
    assert 1 in verdict.cited_source_indices


def test_validate_block_si_FUENTE_no_existe() -> None:
    context = build_llm_context([_norma_match()])
    response = "Según el art. 19 LIRPF [FUENTE 5]."  # solo hay 1 fuente.
    verdict = validate_grounded_response(response, context=context)
    assert verdict.level == GroundingVerdictLevel.BLOCK
    assert any(
        "no existe en el contexto" in i.reason for i in verdict.issues
    )


def test_validate_warn_si_cita_normativa_no_aparece_en_contexto() -> None:
    """Cita el art. 25 LIVA pero solo entregamos chunks del IRPF.

    El citation_guard puede considerar la cita conocida (existe LIVA),
    pero NO está en las fuentes recuperadas: el LLM no debe citarlo."""
    context = build_llm_context([_norma_match()])
    response = "Según el art. 25 LIVA (BOE-A-1992-28740) está exenta."
    verdict = validate_grounded_response(response, context=context)
    assert verdict.level in (
        GroundingVerdictLevel.WARN,
        GroundingVerdictLevel.BLOCK,
    )
    assert any(
        "no aparece en las fuentes" in i.reason
        or "ausente del contexto" in i.reason
        for i in verdict.issues
    )


def test_validate_safe_si_cita_solo_FUENTE_sin_pinpoint_literal() -> None:
    """El LLM puede responder solo con `[FUENTE 1]` sin más; eso es safe."""
    context = build_llm_context([_norma_match()])
    response = "Los gastos están limitados a 300 euros anuales [FUENTE 1]."
    verdict = validate_grounded_response(response, context=context)
    assert verdict.level == GroundingVerdictLevel.SAFE


def test_validate_FUENTE_referencia_se_registra() -> None:
    context = build_llm_context([_norma_match(), _dgt_match()])
    response = (
        "Según [FUENTE 1] y la doctrina de [FUENTE 2], los gastos no son "
        "deducibles."
    )
    verdict = validate_grounded_response(response, context=context)
    assert verdict.cited_source_indices == {1, 2}


def test_validate_cita_BOE_id_de_chunk_es_safe() -> None:
    """Citar el `BOE-A-2006-20764` mencionado en metadata del chunk es safe."""
    context = build_llm_context([_norma_match()])
    response = (
        "El BOE-A-2006-20764 regula el IRPF; el límite es de 300 euros."
    )
    verdict = validate_grounded_response(response, context=context)
    assert verdict.level == GroundingVerdictLevel.SAFE


def test_validate_cita_ECLI_de_sentencia_recuperada_es_safe() -> None:
    context = build_llm_context([_sentencia_match()])
    response = (
        "El TS ha establecido en ECLI:ES:TS:2024:1234 que los gastos no son "
        "deducibles."
    )
    verdict = validate_grounded_response(response, context=context)
    assert verdict.level == GroundingVerdictLevel.SAFE


def test_validate_combina_con_temporal_report() -> None:
    """Si un chunk fue rechazado por filtro temporal pero aparece en el
    contexto entregado al LLM (error del caller), el validator lo
    detecta como BLOCK."""
    norma = _norma_match()
    derogada = VectorMatch(
        chunk=EmbeddedChunk(
            chunk_id="norma::derogada",
            source_type=SourceType.NORMA,
            text="Texto de norma derogada",
            embedding=(0.1,),
            embedding_model="test",
            metadata={
                "boe_id": "BOE-A-1980-1234",
                "effective_from": "1980-01-01",
                "effective_to": "2006-12-31",
            },
        ),
        score=0.9,
    )
    # Aplicamos filtro temporal: la "derogada" se rechaza.
    temporal_report = enforce_temporal_filter(
        [norma, derogada],
        VectorQuery(text="x", fecha_devengo=date(2024, 1, 1)),
        mode=TemporalEnforcementMode.STRICT,
    )
    assert len(temporal_report.rejected) == 1

    # Pero el caller (por error) construye el contexto con AMBAS:
    context = build_llm_context([norma, derogada])
    response = "Según [FUENTE 1] y [FUENTE 2] los gastos no son deducibles."
    verdict = validate_grounded_response(
        response, context=context, temporal_report=temporal_report
    )
    # El validator detecta que FUENTE 2 (derogada) fue rechazada y
    # aparece en el contexto → BLOCK.
    assert verdict.level == GroundingVerdictLevel.BLOCK
    assert any("filtro temporal" in i.reason for i in verdict.issues)


def test_validate_respuesta_sin_citas_es_safe() -> None:
    """Una respuesta narrativa sin citas literales no dispara issues."""
    context = build_llm_context([_norma_match()])
    response = "El contribuyente debe revisar los requisitos aplicables."
    verdict = validate_grounded_response(response, context=context)
    assert verdict.level == GroundingVerdictLevel.SAFE
    assert verdict.cited_source_indices == set()


def test_validate_referencia_FUENTE_case_insensitive() -> None:
    context = build_llm_context([_norma_match()])
    # Variaciones tipográficas comunes.
    response = "según [fuente 1] y [FUENTE 1]."
    verdict = validate_grounded_response(response, context=context)
    # Las dos referencias se reconocen como válidas.
    assert 1 in verdict.cited_source_indices
    assert verdict.level == GroundingVerdictLevel.SAFE
