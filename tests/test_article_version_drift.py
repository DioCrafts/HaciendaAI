"""Tests del diff a nivel timeline por artículo y de la persistencia
del snapshot por artículo."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from hacienda_ai.models import VersionArticulo
from hacienda_ai.rag.consolidated import (
    ArticleSnapshotError,
    ArticleVersionSnapshot,
    article_snapshot_path,
    compute_article_version_drift,
    load_article_snapshot,
    save_article_snapshot,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _v(
    *,
    article_id: str = "a23",
    effective_from: date = date(2007, 1, 1),
    effective_to: date | None = None,
    text: str = "txt",
    modified_by: str | None = None,
) -> VersionArticulo:
    return VersionArticulo(
        norma_boe_id="BOE-A-2006-20764",
        article_id=article_id,
        effective_from=effective_from,
        effective_to=effective_to,
        text=text,
        text_hash=_sha(text),
        modified_by_boe_id=modified_by,
    )


# ---------- compute_article_version_drift: bootstrap ----------


def test_drift_bootstrap_when_no_previous() -> None:
    report = compute_article_version_drift(
        boe_id="BOE-A-2006-20764",
        previous_versions=[],
        current_versions=[_v()],
    )
    assert report.is_bootstrap is True
    assert report.has_changes is False
    assert report.added == ()


# ---------- compute_article_version_drift: added ----------


def test_drift_detects_added_when_new_version_appears() -> None:
    """Tras una reforma, aparece una versión nueva (mismo article_id,
    distinto effective_from). Es `added`."""
    prev = [_v(effective_from=date(2007, 1, 1), text="old")]
    curr = [
        _v(effective_from=date(2007, 1, 1), text="old"),
        _v(effective_from=date(2015, 1, 1), text="new"),
    ]
    r = compute_article_version_drift(
        boe_id="BOE-A-2006-20764",
        previous_versions=prev,
        current_versions=curr,
    )
    assert r.has_changes is True
    assert len(r.added) == 1
    assert r.added[0].kind == "added"
    assert r.added[0].article_id == "a23"
    assert r.added[0].effective_from == date(2015, 1, 1)
    assert r.added[0].current is not None
    assert r.added[0].previous is None


# ---------- compute_article_version_drift: removed ----------


def test_drift_detects_removed_when_version_disappears() -> None:
    """BOE corrige y retira una versión publicada por error. Es `removed`."""
    prev = [
        _v(effective_from=date(2007, 1, 1), text="x"),
        _v(effective_from=date(2015, 1, 1), text="y"),
    ]
    curr = [_v(effective_from=date(2007, 1, 1), text="x")]
    r = compute_article_version_drift(
        boe_id="BOE-A-2006-20764",
        previous_versions=prev,
        current_versions=curr,
    )
    assert r.has_changes is True
    assert len(r.removed) == 1
    assert r.removed[0].kind == "removed"
    assert r.removed[0].effective_from == date(2015, 1, 1)


# ---------- compute_article_version_drift: rewritten ----------


def test_drift_detects_rewritten_when_same_key_distinct_text() -> None:
    """Misma `(article_id, effective_from)`, distinto texto = corrección
    editorial oficial. Drift de tipo `rewritten`."""
    prev = [_v(effective_from=date(2015, 1, 1), text="old text")]
    curr = [_v(effective_from=date(2015, 1, 1), text="corrected text")]
    r = compute_article_version_drift(
        boe_id="BOE-A-2006-20764",
        previous_versions=prev,
        current_versions=curr,
    )
    assert r.has_changes is True
    assert len(r.rewritten) == 1
    assert r.rewritten[0].kind == "rewritten"
    assert r.rewritten[0].previous is not None
    assert r.rewritten[0].current is not None
    assert r.rewritten[0].previous.text_hash != r.rewritten[0].current.text_hash


# ---------- compute_article_version_drift: shifted ----------


def test_drift_detects_shifted_when_effective_to_changes() -> None:
    """Mismo `(article_id, effective_from)`, mismo texto, distinto
    `effective_to` (típicamente de None a una fecha porque otra norma
    posterior la cierra). Drift de tipo `shifted`."""
    prev = [
        _v(
            effective_from=date(2015, 1, 1),
            effective_to=None,
            text="x",
        )
    ]
    curr = [
        _v(
            effective_from=date(2015, 1, 1),
            effective_to=date(2024, 12, 31),
            text="x",
        )
    ]
    r = compute_article_version_drift(
        boe_id="BOE-A-2006-20764",
        previous_versions=prev,
        current_versions=curr,
    )
    assert r.has_changes is True
    assert len(r.shifted) == 1
    assert r.shifted[0].kind == "shifted"
    assert r.shifted[0].previous is not None
    assert r.shifted[0].current is not None
    assert r.shifted[0].previous.effective_to is None
    assert r.shifted[0].current.effective_to == date(2024, 12, 31)


# ---------- compute_article_version_drift: no changes ----------


def test_drift_returns_no_changes_when_identical() -> None:
    versions = [
        _v(effective_from=date(2007, 1, 1), text="a"),
        _v(effective_from=date(2015, 1, 1), text="b"),
    ]
    r = compute_article_version_drift(
        boe_id="BOE-A-2006-20764",
        previous_versions=versions,
        current_versions=versions,
    )
    assert r.has_changes is False
    assert r.added == ()
    assert r.removed == ()
    assert r.rewritten == ()
    assert r.shifted == ()


def test_drift_affected_article_ids() -> None:
    """`affected_article_ids` agrega los article_id de TODAS las categorías
    y los devuelve ordenados sin duplicados."""
    prev = [_v(article_id="a1", effective_from=date(2007, 1, 1), text="x")]
    curr = [
        _v(article_id="a1", effective_from=date(2007, 1, 1), text="x"),
        _v(article_id="a23", effective_from=date(2015, 1, 1), text="y"),
        _v(article_id="a81bis", effective_from=date(2015, 1, 1), text="z"),
    ]
    r = compute_article_version_drift(
        boe_id="BOE-A-2006-20764",
        previous_versions=prev,
        current_versions=curr,
    )
    assert r.affected_article_ids == ("a23", "a81bis")


# ---------- ArticleVersionSnapshot persistence ----------


def test_snapshot_roundtrip_json(tmp_path: Path) -> None:
    snap = ArticleVersionSnapshot(
        boe_id="BOE-A-2006-20764",
        last_checked_at=date(2026, 5, 17),
        reference_date=date(2026, 5, 17),
        versions=(
            _v(article_id="a23", effective_from=date(2015, 1, 1)),
            _v(
                article_id="a23",
                effective_from=date(2007, 1, 1),
                effective_to=date(2014, 12, 31),
                text="old",
            ),
        ),
    )
    path = save_article_snapshot(tmp_path, snap)
    assert path.exists()
    restored = load_article_snapshot(tmp_path, "BOE-A-2006-20764")
    assert restored is not None
    assert restored.boe_id == snap.boe_id
    assert restored.total_versions == 2
    assert restored.article_ids == {"a23"}


def test_snapshot_versions_serialize_ordered_for_diff_estable(
    tmp_path: Path,
) -> None:
    """Las versiones del JSON deben salir ordenadas por
    (article_id, effective_from) para que dos snapshots con el mismo
    contenido en distinto orden produzcan idéntico fichero."""
    snap = ArticleVersionSnapshot(
        boe_id="BOE-A-2006-20764",
        last_checked_at=date(2026, 5, 17),
        reference_date=date(2026, 5, 17),
        versions=(
            _v(article_id="a81bis", effective_from=date(2015, 1, 1)),
            _v(article_id="a23", effective_from=date(2015, 1, 1)),
            _v(
                article_id="a23",
                effective_from=date(2007, 1, 1),
                effective_to=date(2014, 12, 31),
                text="old",
            ),
        ),
    )
    save_article_snapshot(tmp_path, snap)
    payload = json.loads(
        article_snapshot_path(tmp_path, "BOE-A-2006-20764").read_text()
    )
    keys = [(v["article_id"], v["effective_from"]) for v in payload["versions"]]
    assert keys == [
        ("a23", "2007-01-01"),
        ("a23", "2015-01-01"),
        ("a81bis", "2015-01-01"),
    ]


def test_load_article_snapshot_returns_none_when_missing(tmp_path: Path) -> None:
    assert load_article_snapshot(tmp_path, "BOE-A-9999-9999") is None


def test_load_article_snapshot_raises_on_corrupted_json(tmp_path: Path) -> None:
    bad = article_snapshot_path(tmp_path, "BOE-A-2006-20764")
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{ this is not valid json", encoding="utf-8")
    with pytest.raises(ArticleSnapshotError):
        load_article_snapshot(tmp_path, "BOE-A-2006-20764")


def test_load_article_snapshot_raises_on_missing_required_field(
    tmp_path: Path,
) -> None:
    bad = article_snapshot_path(tmp_path, "BOE-A-2006-20764")
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text(json.dumps({"boe_id": "X"}), encoding="utf-8")
    with pytest.raises(ArticleSnapshotError):
        load_article_snapshot(tmp_path, "BOE-A-2006-20764")
