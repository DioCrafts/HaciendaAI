"""Tests de persistencia de snapshots de norma."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from hacienda_ai.rag.consolidated import (
    NormaSnapshot,
    SnapshotError,
    load_snapshot,
    save_snapshot,
    snapshot_path,
)

_HASH = "0123456789abcdef" * 4  # 64 chars


def _snapshot(**overrides: object) -> NormaSnapshot:
    base = dict(
        boe_id="BOE-A-2006-20764",
        last_checked_at=date(2024, 1, 1),
        reference_date=date(2024, 1, 1),
        consolidated_articles={"a1": _HASH, "a2": _HASH.upper()},
    )
    base.update(overrides)
    return NormaSnapshot(**base)  # type: ignore[arg-type]


def test_snapshot_path_es_canonico(tmp_path: Path) -> None:
    p = snapshot_path(tmp_path, "BOE-A-2006-20764")
    assert p == tmp_path / "BOE-A-2006-20764.json"


def test_round_trip_save_load(tmp_path: Path) -> None:
    snap = _snapshot()
    save_snapshot(tmp_path, snap)
    loaded = load_snapshot(tmp_path, snap.boe_id)
    assert loaded is not None
    assert loaded.boe_id == snap.boe_id
    assert loaded.reference_date == snap.reference_date
    # Los hashes se normalizan a minúsculas al cargar.
    assert loaded.consolidated_articles == {"a1": _HASH, "a2": _HASH.lower()}


def test_load_inexistente_devuelve_none(tmp_path: Path) -> None:
    assert load_snapshot(tmp_path, "BOE-A-2099-9999") is None


def test_save_es_atomico_y_ordenado(tmp_path: Path) -> None:
    save_snapshot(tmp_path, _snapshot(consolidated_articles={"z": _HASH, "a": _HASH}))
    data = json.loads(
        (tmp_path / "BOE-A-2006-20764.json").read_text(encoding="utf-8")
    )
    # Las claves se serializan ordenadas para diffs estables.
    assert list(data["consolidated_articles"].keys()) == ["a", "z"]
    # No queda el fichero .tmp tras el rename atómico.
    assert not (tmp_path / "BOE-A-2006-20764.json.tmp").exists()


def test_load_falla_con_json_invalido(tmp_path: Path) -> None:
    path = tmp_path / "BOE-A-2006-20764.json"
    path.write_text("no es json")
    with pytest.raises(SnapshotError):
        load_snapshot(tmp_path, "BOE-A-2006-20764")


def test_load_falla_con_hash_invalido(tmp_path: Path) -> None:
    path = tmp_path / "BOE-A-2006-20764.json"
    path.write_text(
        json.dumps(
            {
                "boe_id": "BOE-A-2006-20764",
                "last_checked_at": "2024-01-01",
                "reference_date": "2024-01-01",
                "consolidated_articles": {"a1": "no-es-un-hash"},
            }
        )
    )
    with pytest.raises(SnapshotError):
        load_snapshot(tmp_path, "BOE-A-2006-20764")


def test_load_falla_con_fecha_invalida(tmp_path: Path) -> None:
    path = tmp_path / "BOE-A-2006-20764.json"
    path.write_text(
        json.dumps(
            {
                "boe_id": "BOE-A-2006-20764",
                "last_checked_at": "no-es-fecha",
                "reference_date": "2024-01-01",
                "consolidated_articles": {},
            }
        )
    )
    with pytest.raises(SnapshotError):
        load_snapshot(tmp_path, "BOE-A-2006-20764")
