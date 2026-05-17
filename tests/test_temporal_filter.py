"""Tests del filtro temporal duro."""

from __future__ import annotations

from datetime import date

import pytest

from hacienda_ai.rag.temporal import (
    StrictTemporalFilterError,
    TemporalEnforcementMode,
    enforce_temporal_filter,
    require_fecha_devengo,
)
from hacienda_ai.rag.vector import (
    EmbeddedChunk,
    SourceType,
    VectorMatch,
    VectorQuery,
)


def _match(
    chunk_id: str,
    *,
    effective_from: str | None = None,
    effective_to: str | None = None,
) -> VectorMatch:
    metadata: dict = {}
    if effective_from is not None:
        metadata["effective_from"] = effective_from
    if effective_to is not None:
        metadata["effective_to"] = effective_to
    return VectorMatch(
        chunk=EmbeddedChunk(
            chunk_id=chunk_id,
            source_type=SourceType.NORMA,
            text="texto",
            embedding=(0.1,),
            embedding_model="test",
            metadata=metadata,
        ),
        score=1.0,
    )


# ---------- require_fecha_devengo ----------


def test_require_fecha_devengo_explicita_pasa() -> None:
    query = VectorQuery(text="x", fecha_devengo=date(2024, 1, 1))
    fecha, explicit = require_fecha_devengo(
        query, mode=TemporalEnforcementMode.STRICT
    )
    assert fecha == date(2024, 1, 1)
    assert explicit


def test_strict_sin_fecha_devengo_lanza() -> None:
    query = VectorQuery(text="x")
    with pytest.raises(StrictTemporalFilterError) as exc_info:
        require_fecha_devengo(query, mode=TemporalEnforcementMode.STRICT)
    assert "STRICT" in str(exc_info.value)


def test_warn_sin_fecha_devengo_asume_today() -> None:
    query = VectorQuery(text="x")
    fecha, explicit = require_fecha_devengo(
        query,
        mode=TemporalEnforcementMode.WARN,
        today=date(2024, 5, 1),
    )
    assert fecha == date(2024, 5, 1)
    assert not explicit


def test_off_sin_fecha_devengo_asume_today() -> None:
    query = VectorQuery(text="x")
    fecha, explicit = require_fecha_devengo(
        query,
        mode=TemporalEnforcementMode.OFF,
        today=date(2024, 5, 1),
    )
    assert fecha == date(2024, 5, 1)
    assert not explicit


# ---------- enforce_temporal_filter ----------


def test_acepta_chunk_vigente_en_fecha_devengo() -> None:
    matches = [
        _match(
            "vigente",
            effective_from="2015-01-01",
            effective_to=None,
        )
    ]
    report = enforce_temporal_filter(
        matches,
        VectorQuery(text="x", fecha_devengo=date(2024, 1, 1)),
        mode=TemporalEnforcementMode.STRICT,
    )
    assert len(report.accepted) == 1
    assert report.rejected == []
    assert report.fecha_devengo_explicit


def test_rechaza_chunk_de_norma_posterior_al_devengo() -> None:
    matches = [
        _match("futura", effective_from="2030-01-01"),
    ]
    report = enforce_temporal_filter(
        matches,
        VectorQuery(text="x", fecha_devengo=date(2024, 1, 1)),
        mode=TemporalEnforcementMode.STRICT,
    )
    assert report.accepted == []
    assert len(report.rejected) == 1
    assert "posterior" in report.rejected[0][1]


def test_rechaza_chunk_de_norma_derogada_antes_del_devengo() -> None:
    matches = [
        _match(
            "derogada",
            effective_from="2000-01-01",
            effective_to="2006-12-31",
        )
    ]
    report = enforce_temporal_filter(
        matches,
        VectorQuery(text="x", fecha_devengo=date(2024, 1, 1)),
        mode=TemporalEnforcementMode.STRICT,
    )
    assert report.accepted == []
    assert "derogada" in report.rejected[0][1]


def test_strict_rechaza_chunk_atemporal() -> None:
    """Sin `effective_from`, no podemos verificar vigencia."""
    matches = [_match("manual")]  # sin fechas.
    report = enforce_temporal_filter(
        matches,
        VectorQuery(text="x", fecha_devengo=date(2024, 1, 1)),
        mode=TemporalEnforcementMode.STRICT,
    )
    assert report.accepted == []
    assert "effective_from ausente" in report.rejected[0][1]


def test_warn_acepta_chunk_atemporal_con_disclaimer() -> None:
    matches = [_match("manual")]
    report = enforce_temporal_filter(
        matches,
        VectorQuery(text="x", fecha_devengo=date(2024, 1, 1)),
        mode=TemporalEnforcementMode.WARN,
    )
    assert len(report.accepted) == 1
    assert len(report.atemporal_with_disclaimer) == 1


def test_off_acepta_chunk_atemporal_sin_disclaimer() -> None:
    matches = [_match("manual")]
    report = enforce_temporal_filter(
        matches,
        VectorQuery(text="x", fecha_devengo=date(2024, 1, 1)),
        mode=TemporalEnforcementMode.OFF,
    )
    assert len(report.accepted) == 1
    assert report.atemporal_with_disclaimer == []


def test_report_warnings_incluye_fecha_asumida() -> None:
    report = enforce_temporal_filter(
        [_match("vigente", effective_from="2015-01-01")],
        VectorQuery(text="x"),
        mode=TemporalEnforcementMode.WARN,
        today=date(2024, 5, 1),
    )
    warnings = report.warnings_text()
    assert any("asumida" in w for w in warnings)
    assert report.has_warnings


def test_report_sin_warnings_si_fecha_explicita_y_chunks_temporales() -> None:
    report = enforce_temporal_filter(
        [_match("vigente", effective_from="2015-01-01")],
        VectorQuery(text="x", fecha_devengo=date(2024, 1, 1)),
        mode=TemporalEnforcementMode.STRICT,
    )
    assert not report.has_warnings


def test_filter_aplica_a_multiples_chunks_mezclados() -> None:
    matches = [
        _match("vigente", effective_from="2015-01-01"),
        _match("derogada", effective_from="2000-01-01", effective_to="2006-12-31"),
        _match("futura", effective_from="2030-01-01"),
        _match("atemporal"),
    ]
    report = enforce_temporal_filter(
        matches,
        VectorQuery(text="x", fecha_devengo=date(2024, 1, 1)),
        mode=TemporalEnforcementMode.STRICT,
    )
    # Solo "vigente" sobrevive en STRICT.
    accepted_ids = [m.chunk.chunk_id for m in report.accepted]
    assert accepted_ids == ["vigente"]
    assert len(report.rejected) == 3


def test_filter_acepta_chunk_con_effective_to_igual_devengo() -> None:
    """`effective_to == fecha_devengo` significa "vigente HASTA esa fecha
    incluida": el chunk debe pasar."""
    matches = [
        _match(
            "vigente_hasta_hoy",
            effective_from="2015-01-01",
            effective_to="2024-01-01",
        )
    ]
    report = enforce_temporal_filter(
        matches,
        VectorQuery(text="x", fecha_devengo=date(2024, 1, 1)),
        mode=TemporalEnforcementMode.STRICT,
    )
    assert len(report.accepted) == 1


def test_filter_acepta_chunk_con_effective_from_igual_devengo() -> None:
    """`effective_from == fecha_devengo` significa "vigente DESDE esa fecha
    incluida": el chunk debe pasar."""
    matches = [
        _match("nuevo_hoy", effective_from="2024-01-01"),
    ]
    report = enforce_temporal_filter(
        matches,
        VectorQuery(text="x", fecha_devengo=date(2024, 1, 1)),
        mode=TemporalEnforcementMode.STRICT,
    )
    assert len(report.accepted) == 1


def test_filter_ignora_metadata_temporal_invalida() -> None:
    """Si effective_from no es parseable, se trata como ausente."""
    matches = [
        VectorMatch(
            chunk=EmbeddedChunk(
                chunk_id="malformed",
                source_type=SourceType.NORMA,
                text="t",
                embedding=(0.0,),
                embedding_model="test",
                metadata={"effective_from": "no es fecha"},
            ),
            score=1.0,
        )
    ]
    report = enforce_temporal_filter(
        matches,
        VectorQuery(text="x", fecha_devengo=date(2024, 1, 1)),
        mode=TemporalEnforcementMode.STRICT,
    )
    # Tratado como ausente → STRICT rechaza.
    assert report.accepted == []
    assert "ausente" in report.rejected[0][1]
