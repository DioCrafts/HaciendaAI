"""Tests del paquete `hacienda_ai.storage` (Sprint 1 #3).

Cubren los dos repos y la cascada de resolución de `db_path`. Los tests de
persistencia entre arranques viven en `tests/test_api.py` (necesitan el
TestClient para validar la promesa del endpoint).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from hacienda_ai.models import TaxProfile
from hacienda_ai.storage import (
    DEFAULT_DB_PATH,
    EvaluationsRepo,
    ProfilesRepo,
    init_db,
    resolve_db_path,
)
from hacienda_ai.storage.db import ENV_DB_PATH


@pytest.fixture
def repos() -> tuple[ProfilesRepo, EvaluationsRepo]:
    """Pareja de repos sobre la misma conexión `:memory:`. La conexión vive
    mientras vivan los repos; pytest la garbage-collecta al terminar el
    test."""
    conn = init_db(":memory:")
    return ProfilesRepo(conn), EvaluationsRepo(conn)


def _profile() -> TaxProfile:
    return TaxProfile.from_dict({
        "tax_year": 2025,
        "region": "Madrid",
        "devengo_date": "2025-12-31",
        "personal": {"has_disability": False},
        "family": {"children_count": 1},
        "income": {"work_gross": 30000},
        "documents": ["Libro de familia o certificado de convivencia"],
    })


def test_resolve_db_path_prefers_explicit_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_DB_PATH, "/from/env.db")
    assert resolve_db_path("/explicit/arg.db") == "/explicit/arg.db"


def test_resolve_db_path_falls_back_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_DB_PATH, "/from/env.db")
    assert resolve_db_path(None) == "/from/env.db"


def test_resolve_db_path_defaults_to_home_when_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_DB_PATH, raising=False)
    assert resolve_db_path(None) == str(DEFAULT_DB_PATH)


def test_resolve_db_path_passes_through_memory_marker() -> None:
    assert resolve_db_path(":memory:") == ":memory:"


def test_init_db_creates_schema(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    conn = init_db(db)
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"profiles", "evaluations"}.issubset(tables)


def test_init_db_creates_missing_parent_directory(tmp_path: Path) -> None:
    """Si el path apunta a un directorio que aún no existe, `init_db` lo
    crea. Sin esto, el default `~/.hacienda-ai/hacienda.db` fallaría en
    una instalación fresca."""
    db = tmp_path / "deep" / "subdir" / "hacienda.db"
    assert not db.parent.exists()
    init_db(db)
    assert db.parent.exists()
    assert db.exists()


def test_profiles_repo_round_trip(repos: tuple[ProfilesRepo, EvaluationsRepo]) -> None:
    profiles_repo, _ = repos
    profile = _profile()
    profiles_repo.save("abc123", profile)
    recovered = profiles_repo.get("abc123")
    assert recovered is not None
    assert recovered.tax_year == 2025
    assert recovered.region == "Madrid"
    assert recovered.devengo_date == date(2025, 12, 31)
    assert recovered.family["children_count"] == 1


def test_profiles_repo_returns_none_for_unknown_id(
    repos: tuple[ProfilesRepo, EvaluationsRepo],
) -> None:
    profiles_repo, _ = repos
    assert profiles_repo.get("does-not-exist") is None


def test_evaluations_repo_round_trip(
    repos: tuple[ProfilesRepo, EvaluationsRepo],
) -> None:
    profiles_repo, evals_repo = repos
    # FK enforced (PRAGMA foreign_keys=ON): primero guardamos el perfil.
    profiles_repo.save("p1", _profile())
    payload: dict[str, Any] = {
        "evaluation_id": "e1",
        "profile_id": "p1",
        "evaluated_at": "2026-05-16T10:00:00+00:00",
        "devengo_date": "2025-12-31",
        "corpus": {"fingerprint_sha256": "f" * 64, "count": 76},
        "evaluations": [{"deduction_id": "x", "status": "applies"}],
    }
    evals_repo.save("e1", "p1", payload)
    recovered = evals_repo.get("e1")
    assert recovered == payload


def test_evaluations_repo_foreign_key_blocks_orphan_insert(
    repos: tuple[ProfilesRepo, EvaluationsRepo],
) -> None:
    """`PRAGMA foreign_keys=ON` debe impedir guardar una evaluación que
    referencie a un perfil inexistente. Si esta protección se cae, la
    base se llena de filas huérfanas silenciosamente."""
    import sqlite3 as _sqlite3
    _, evals_repo = repos
    with pytest.raises(_sqlite3.IntegrityError):
        evals_repo.save(
            "e1",
            "perfil-fantasma",
            {
                "evaluated_at": "2026-05-16T10:00:00+00:00",
                "devengo_date": "2025-12-31",
                "corpus": {"fingerprint_sha256": ""},
            },
        )


def test_persistence_across_init_db_calls_on_same_file(tmp_path: Path) -> None:
    """Imitación de "reinicio del proceso": dos llamadas a `init_db` sobre
    el mismo archivo deben ver los datos persistidos por la primera. Es
    el invariante crítico que justifica salir de los dicts en memoria."""
    db = tmp_path / "persist.db"

    conn1 = init_db(db)
    ProfilesRepo(conn1).save("p1", _profile())
    conn1.close()

    conn2 = init_db(db)
    recovered = ProfilesRepo(conn2).get("p1")
    assert recovered is not None
    assert recovered.tax_year == 2025
    assert recovered.region == "Madrid"
