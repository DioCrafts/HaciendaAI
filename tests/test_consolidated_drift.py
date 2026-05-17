"""Tests del detector de drift entre snapshots."""

from __future__ import annotations

from datetime import date

from hacienda_ai.rag.consolidated import (
    NormaSnapshot,
    compute_norma_drift,
)

_H1 = "a" * 64
_H2 = "b" * 64
_H3 = "c" * 64


def _snapshot(articles: dict[str, str]) -> NormaSnapshot:
    return NormaSnapshot(
        boe_id="BOE-A-2006-20764",
        last_checked_at=date(2024, 1, 1),
        reference_date=date(2024, 1, 1),
        consolidated_articles=dict(articles),
    )


def test_bootstrap_no_es_drift() -> None:
    """Sin snapshot previo, NO se considera drift aunque haya artículos."""
    report = compute_norma_drift(
        boe_id="BOE-A-2006-20764",
        reference_date=date(2024, 1, 1),
        current_hashes={"a1": _H1, "a2": _H2},
        previous=None,
        today=date(2024, 1, 1),
    )
    assert report.is_bootstrap
    assert not report.has_changes
    assert report.added == ()
    assert report.removed == ()
    assert report.modified == ()
    # El new_snapshot incluye TODOS los artículos calculados.
    assert report.new_snapshot.article_ids == {"a1", "a2"}


def test_sin_cambios_no_es_drift() -> None:
    prev = _snapshot({"a1": _H1, "a2": _H2})
    report = compute_norma_drift(
        boe_id="BOE-A-2006-20764",
        reference_date=date(2024, 1, 1),
        current_hashes={"a1": _H1, "a2": _H2},
        previous=prev,
        today=date(2024, 1, 1),
    )
    assert not report.is_bootstrap
    assert not report.has_changes


def test_articulo_modificado() -> None:
    prev = _snapshot({"a1": _H1, "a2": _H2})
    report = compute_norma_drift(
        boe_id="BOE-A-2006-20764",
        reference_date=date(2024, 1, 1),
        current_hashes={"a1": _H1, "a2": _H3},  # a2 cambió.
        previous=prev,
        today=date(2024, 1, 1),
    )
    assert report.has_changes
    assert len(report.modified) == 1
    drift = report.modified[0]
    assert drift.block_id == "a2"
    assert drift.kind == "modified"
    assert drift.previous_hash == _H2
    assert drift.current_hash == _H3
    assert report.added == () and report.removed == ()


def test_articulo_anadido() -> None:
    prev = _snapshot({"a1": _H1})
    report = compute_norma_drift(
        boe_id="BOE-A-2006-20764",
        reference_date=date(2024, 1, 1),
        current_hashes={"a1": _H1, "a81bis": _H2},  # nuevo
        previous=prev,
        today=date(2024, 1, 1),
    )
    assert report.has_changes
    assert len(report.added) == 1
    assert report.added[0].block_id == "a81bis"
    assert report.added[0].kind == "added"
    assert report.added[0].previous_hash is None


def test_articulo_eliminado() -> None:
    prev = _snapshot({"a1": _H1, "a2": _H2})
    report = compute_norma_drift(
        boe_id="BOE-A-2006-20764",
        reference_date=date(2024, 1, 1),
        current_hashes={"a1": _H1},
        previous=prev,
        today=date(2024, 1, 1),
    )
    assert report.has_changes
    assert len(report.removed) == 1
    assert report.removed[0].block_id == "a2"
    assert report.removed[0].kind == "removed"
    assert report.removed[0].current_hash is None


def test_renumeracion_aparece_como_removed_mas_added() -> None:
    """Renombrar DT 1ª → DT 2ª se ve como 1 removed + 1 added, no modified.

    La herramienta no infiere renombrado: solo señala. La revisión humana
    decide si es la misma redacción reubicada o un cambio sustantivo.
    """
    prev = _snapshot({"dt1": _H1})
    report = compute_norma_drift(
        boe_id="BOE-A-2006-20764",
        reference_date=date(2024, 1, 1),
        current_hashes={"dt2": _H1},  # mismo hash, otra clave.
        previous=prev,
        today=date(2024, 1, 1),
    )
    assert len(report.added) == 1 and report.added[0].block_id == "dt2"
    assert len(report.removed) == 1 and report.removed[0].block_id == "dt1"
    assert report.modified == ()


def test_affected_block_ids_consolida_los_tres_tipos() -> None:
    prev = _snapshot({"a1": _H1, "a2": _H2})
    report = compute_norma_drift(
        boe_id="BOE-A-2006-20764",
        reference_date=date(2024, 1, 1),
        current_hashes={"a1": _H3, "a3": _H2},
        previous=prev,
        today=date(2024, 1, 1),
    )
    assert set(report.affected_block_ids) == {"a1", "a2", "a3"}
