"""Inicialización del store SQLite.

`init_db(path)` abre la conexión, habilita claves foráneas y crea las
tablas si no existen. `resolve_db_path()` aplica la cascada de defaults:

1. Si `db_path` se pasa explícitamente, se usa.
2. Si la env var `HACIENDA_AI_DB_PATH` está fijada, se usa.
3. Si no, `~/.hacienda-ai/hacienda.db` (se crea el directorio en init).

`:memory:` es válido como path en tests, pero no se persiste entre
arranques del proceso (es la propia naturaleza de SQLite memoria).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path.home() / ".hacienda-ai" / "hacienda.db"
ENV_DB_PATH = "HACIENDA_AI_DB_PATH"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id            TEXT PRIMARY KEY,
    tax_year      INTEGER NOT NULL,
    region        TEXT    NOT NULL,
    devengo_date  TEXT,
    payload_json  TEXT    NOT NULL,
    created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluations (
    id                 TEXT PRIMARY KEY,
    profile_id         TEXT NOT NULL REFERENCES profiles(id),
    evaluated_at       TEXT NOT NULL,
    devengo_date       TEXT NOT NULL,
    corpus_fingerprint TEXT NOT NULL,
    payload_json       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evaluations_profile_id ON evaluations(profile_id);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    history_json TEXT NOT NULL
);
"""


def resolve_db_path(db_path: str | Path | None) -> str:
    """Resuelve qué path usar: argumento explícito → env var → default.

    Devuelve siempre un string para pasar tal cual a `sqlite3.connect`,
    incluyendo el caso especial `":memory:"` que sqlite reconoce."""
    if db_path is not None:
        return str(db_path)
    from_env = os.environ.get(ENV_DB_PATH)
    if from_env:
        return from_env
    return str(DEFAULT_DB_PATH)


def init_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Abre la conexión SQLite y garantiza que el esquema existe.

    Activa `foreign_keys=ON` (SQLite no las hace cumplir por defecto) y
    `check_same_thread=False` para que FastAPI/uvicorn puedan usar la
    misma conexión desde varios hilos. Single-process; si en el futuro
    se sirve con varios workers, hay que migrar a Postgres o a un pool.
    """
    resolved = resolve_db_path(db_path)
    if resolved != ":memory:":
        Path(resolved).expanduser().parent.mkdir(parents=True, exist_ok=True)
        resolved = str(Path(resolved).expanduser())
    conn = sqlite3.connect(resolved, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn
