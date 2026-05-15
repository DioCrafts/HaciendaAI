"""Logging estructurado del motor sin información personal identificable.

Decisiones de diseño (Fase 7 RGPD)
---------------------------------
- Los logs del motor NUNCA registran importes del perfil (income, expenses,
  taxable_base, cuota), nombres, NIFs, ni texto literal de documentos. Los
  importes calculados por reglas tampoco se loguean: bastan los `status` y
  contadores agregados para reconstruir el comportamiento del motor.
- La región (CCAA) puede ser sensible si se combina con otros datos, así
  que se loguea con un `region_hash` HMAC-SHA256 truncado a 8 hex. La sal
  del HMAC se genera al arrancar el proceso con `secrets.token_bytes(16)`:
  los hashes son comparables DENTRO de la misma ejecución (útil para
  detectar repetidos) pero NO cross-session, eliminando la posibilidad de
  correlar trazas históricas.
- IDs de deducciones del corpus son públicos por diseño: se loguean
  literalmente. Lo mismo `tax_year` y `filing_mode` (no son PII por sí
  solos en el universo de IRPF).
- Configurable por variables de entorno:
    HACIENDA_AI_LOG_LEVEL  (DEBUG|INFO|WARNING|ERROR, default INFO)
    HACIENDA_AI_LOG_FORMAT (text|json, default text)
  Para uso programático (tests, API), `configure_logging()` acepta los
  mismos parámetros explícitamente y es idempotente.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
from hashlib import sha256
from typing import Any

LOG_LEVEL_ENV = "HACIENDA_AI_LOG_LEVEL"
LOG_FORMAT_ENV = "HACIENDA_AI_LOG_FORMAT"
LOGGER_NAME = "hacienda_ai"

# Sal de proceso: idéntica en todos los logs de la misma ejecución, irrecuperable
# tras reiniciar. Evita que dos sesiones distintas produzcan el mismo hash para
# la misma región.
_PROCESS_SALT: bytes = secrets.token_bytes(16)


class _JsonFormatter(logging.Formatter):
    """Formatter que emite cada record como un objeto JSON en una línea."""

    _STANDARD_KEYS = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "taskName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key in self._STANDARD_KEYS or key.startswith("_"):
                continue
            payload[key] = value
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, ensure_ascii=False, default=str)


_configured: bool = False


def configure_logging(*, level: str | None = None, fmt: str | None = None) -> None:
    """Configura el logger raíz `hacienda_ai`. Idempotente: la segunda
    llamada con los mismos parámetros no añade handlers."""
    global _configured
    chosen_level = (level or os.environ.get(LOG_LEVEL_ENV) or "INFO").upper()
    chosen_format = (fmt or os.environ.get(LOG_FORMAT_ENV) or "text").lower()

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(chosen_level)

    # Evita handlers duplicados al reconfigurar (típico en tests).
    for handler in list(logger.handlers):
        logger.removeHandler(handler)

    handler = logging.StreamHandler()
    if chosen_format == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
    logger.addHandler(handler)
    logger.propagate = False
    _configured = True


def get_logger(name: str | None = None) -> logging.Logger:
    """Devuelve un sub-logger del namespace `hacienda_ai`."""
    if not _configured:
        configure_logging()
    if name and not name.startswith(LOGGER_NAME):
        name = f"{LOGGER_NAME}.{name}"
    return logging.getLogger(name or LOGGER_NAME)


def hash_region(region: str | None) -> str:
    """Devuelve un hash HMAC-SHA256 truncado a 8 hex de la región. La sal
    es de proceso (no persistente), de manera que los hashes son
    comparables sólo dentro de la misma ejecución."""
    if not region:
        return "none"
    digest = hmac.new(_PROCESS_SALT, region.strip().lower().encode("utf-8"), sha256).hexdigest()
    return digest[:8]
