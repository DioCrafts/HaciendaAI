"""Persistencia mínima del API HTTP.

Repositorios SQLite (`sqlite3` puro, sin dependencia nueva) para `profiles`
y `evaluations`, reemplazando los `dict` en memoria que vivían en
`api/app.py`. El path de la base de datos se inyecta en `create_app()`
o se resuelve por env var `HACIENDA_AI_DB_PATH`; en tests se pasa
`:memory:`.

El esquema es deliberadamente simple: un PK textual (UUID hex), un puñado
de columnas tipadas para queries futuras (tax_year, region, devengo_date,
corpus_fingerprint), y el payload completo serializado como JSON en
`payload_json`. Una persona puede inspeccionar la DB con sqlite3 CLI sin
necesitar el código del producto.
"""

from .db import DEFAULT_DB_PATH, init_db, resolve_db_path
from .repositories import ChatSessionsRepo, EvaluationsRepo, ProfilesRepo

__all__ = [
    "ChatSessionsRepo",
    "DEFAULT_DB_PATH",
    "EvaluationsRepo",
    "ProfilesRepo",
    "init_db",
    "resolve_db_path",
]
