"""Tests del log append-only de invalidaciones RAG."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hacienda_ai.rag.cache import Invalidation, JsonAuditLog


def _clock_at(year: int, month: int, day: int):
    """Reloj fijo UTC para timestamps deterministas."""
    fixed = datetime(year, month, day, 12, 0, 0, tzinfo=timezone.utc)
    return lambda: fixed


def test_invalidate_persiste_entrada_en_log_vacio(tmp_path: Path) -> None:
    log = JsonAuditLog(path=tmp_path / "log.json", _clock=_clock_at(2024, 1, 1))
    entry = log.invalidate(
        boe_id="BOE-A-2006-20764",
        articles=["a19", "a81bis"],
        reason="test",
    )
    assert isinstance(entry, Invalidation)
    assert entry.boe_id == "BOE-A-2006-20764"
    assert entry.articles == ("a19", "a81bis")
    # Persistido en disco.
    data = json.loads((tmp_path / "log.json").read_text(encoding="utf-8"))
    assert len(data["invalidations"]) == 1
    assert data["invalidations"][0]["timestamp_utc"].startswith("2024-01-01")


def test_invalidate_deduplica_articulos_repetidos(tmp_path: Path) -> None:
    log = JsonAuditLog(path=tmp_path / "log.json", _clock=_clock_at(2024, 1, 1))
    entry = log.invalidate(
        boe_id="BOE-A-2006-20764",
        articles=["a19", "a19", "a81bis", "a19"],
        reason="test",
    )
    assert entry.articles == ("a19", "a81bis")


def test_invalidate_append_no_sobrescribe(tmp_path: Path) -> None:
    log = JsonAuditLog(path=tmp_path / "log.json", _clock=_clock_at(2024, 1, 1))
    log.invalidate(boe_id="BOE-A-2006-20764", articles=["a19"], reason="r1")
    log.invalidate(boe_id="BOE-A-2014-12328", articles=["a10"], reason="r2")
    data = json.loads((tmp_path / "log.json").read_text(encoding="utf-8"))
    assert len(data["invalidations"]) == 2
    assert {e["boe_id"] for e in data["invalidations"]} == {
        "BOE-A-2006-20764",
        "BOE-A-2014-12328",
    }


def test_recent_invalidations_devuelve_ultimas_en_orden_descendente(
    tmp_path: Path,
) -> None:
    log = JsonAuditLog(path=tmp_path / "log.json", _clock=_clock_at(2024, 1, 1))
    log.invalidate(boe_id="A", articles=["x"], reason="r1")
    log.invalidate(boe_id="B", articles=["y"], reason="r2")
    log.invalidate(boe_id="C", articles=["z"], reason="r3")
    recent = log.recent_invalidations(limit=2)
    # Los 2 más recientes en orden cronológico inverso.
    assert [r.boe_id for r in recent] == ["C", "B"]


def test_all_for_filtra_por_boe_id(tmp_path: Path) -> None:
    log = JsonAuditLog(path=tmp_path / "log.json", _clock=_clock_at(2024, 1, 1))
    log.invalidate(boe_id="A", articles=["x"], reason="r1")
    log.invalidate(boe_id="B", articles=["y"], reason="r2")
    log.invalidate(boe_id="A", articles=["z"], reason="r3")
    entries = log.all_for("A")
    assert len(entries) == 2
    assert {a for e in entries for a in e.articles} == {"x", "z"}


def test_log_corrupto_levanta_value_error(tmp_path: Path) -> None:
    path = tmp_path / "log.json"
    path.write_text("no es json")
    log = JsonAuditLog(path=path)
    with pytest.raises(ValueError):
        log.invalidate(boe_id="A", articles=["x"], reason="r1")


def test_invalidation_round_trip_json() -> None:
    original = Invalidation(
        timestamp_utc="2024-01-01T12:00:00+00:00",
        boe_id="BOE-A-2006-20764",
        articles=("a1", "a2"),
        reason="manual",
    )
    restored = Invalidation.from_json(original.to_json())
    assert restored == original
