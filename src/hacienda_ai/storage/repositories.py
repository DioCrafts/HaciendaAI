"""Repositorios de perfiles y evaluaciones.

Sustituyen los `dict[str, ...]` en memoria que vivían en `api/app.py`. Cada
repo recibe la conexión en construcción y la usa para queries puntuales;
no abre conexiones nuevas. La conexión la posee `create_app()`.

El payload del perfil/evaluación se serializa íntegro como JSON en la
columna `payload_json`; las columnas tipadas (`tax_year`, `region`,
`devengo_date`, `corpus_fingerprint`) se duplican para soportar queries
analíticas futuras (listar perfiles 2025 de un cliente, etc.) sin
parsear el JSON.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from ..models import TaxProfile


class ProfilesRepo:
    """Persistencia de `TaxProfile`. La identidad la pone el caller (UUID hex)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(self, profile_id: str, profile: TaxProfile) -> None:
        payload = profile.to_dict()
        self._conn.execute(
            """
            INSERT INTO profiles (id, tax_year, region, devengo_date,
                                  payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                profile_id,
                profile.tax_year,
                profile.region,
                profile.devengo_date.isoformat() if profile.devengo_date else None,
                json.dumps(payload, ensure_ascii=False),
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
        self._conn.commit()

    def get(self, profile_id: str) -> TaxProfile | None:
        row = self._conn.execute(
            "SELECT payload_json FROM profiles WHERE id = ?",
            (profile_id,),
        ).fetchone()
        if row is None:
            return None
        payload: dict[str, Any] = json.loads(row["payload_json"])
        return TaxProfile.from_dict(payload)


class EvaluationsRepo:
    """Persistencia del payload completo de cada evaluación.

    El payload es el mismo `dict` que el endpoint devuelve al cliente, así
    que `get` reconstruye la respuesta original sin transformaciones."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def save(
        self,
        evaluation_id: str,
        profile_id: str,
        payload: dict[str, Any],
    ) -> None:
        corpus = payload.get("corpus") or {}
        self._conn.execute(
            """
            INSERT INTO evaluations (id, profile_id, evaluated_at,
                                     devengo_date, corpus_fingerprint,
                                     payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation_id,
                profile_id,
                payload["evaluated_at"],
                payload["devengo_date"],
                corpus.get("fingerprint_sha256", ""),
                json.dumps(payload, ensure_ascii=False),
            ),
        )
        self._conn.commit()

    def get(self, evaluation_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT payload_json FROM evaluations WHERE id = ?",
            (evaluation_id,),
        ).fetchone()
        if row is None:
            return None
        result: dict[str, Any] = json.loads(row["payload_json"])
        return result
