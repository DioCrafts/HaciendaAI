"""Validadores y utilidades compartidas por los modelos."""

from __future__ import annotations

from datetime import date
from typing import Any


class ValidationError(ValueError):
    """Error de validación de datos fiscales, reglas o normas."""


def require_keys(data: dict[str, Any], keys: list[str], context: str) -> None:
    missing = [key for key in keys if key not in data]
    if missing:
        raise ValidationError(f"Faltan campos obligatorios en {context}: {', '.join(missing)}")


def as_non_empty_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} debe ser texto no vacío")
    return value.strip()


def as_optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} debe ser texto o null")
    return value.strip() or None


def as_optional_number(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} debe ser numérico o null")
    return float(value)


def as_list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError(f"{field_name} debe ser una lista")
    return value


_HEX_CHARS = frozenset("0123456789abcdef")


def validate_content_hash(value: str | None) -> str | None:
    """Valida formato SHA-256 hex (64 caracteres) y normaliza a minúsculas."""
    if value is None:
        return None
    normalized = value.lower()
    if len(normalized) != 64 or any(c not in _HEX_CHARS for c in normalized):
        raise ValidationError(
            "content_hash debe ser SHA-256 en hexadecimal (64 caracteres)"
        )
    return normalized


def parse_iso_date(value: Any, field_name: str) -> date | None:
    """Parsea fechas en formato ISO 8601 (YYYY-MM-DD); admite None."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValidationError(
            f"{field_name} debe ser fecha ISO 8601 (YYYY-MM-DD) o null"
        )
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValidationError(
            f"{field_name} debe ser fecha ISO 8601 (YYYY-MM-DD), recibido: {value!r}"
        ) from exc


def require_iso_date(value: Any, field_name: str) -> date:
    parsed = parse_iso_date(value, field_name)
    if parsed is None:
        raise ValidationError(f"{field_name} es obligatorio")
    return parsed


# Prefijos de boletines oficiales autonómicos/forales reconocidos. Una cita
# con uno de estos prefijos se considera anclada a fuente oficial aunque aún
# no exista verificador SHA-256 para ese boletín (la API consolidada de
# www.boe.es solo cubre normativa estatal). El verificador específico por
# boletín queda como trabajo pendiente; mientras tanto, el motor acepta
# `validation_status="validada"` con boe_id de un boletín de esta lista y
# `content_hash=None`.
REGIONAL_BULLETIN_PREFIXES = (
    "BOCM-",   # Boletín Oficial de la Comunidad de Madrid
    "DOGC-",   # Diari Oficial de la Generalitat de Catalunya
    "DOCV-",   # Diari Oficial de la Comunitat Valenciana
    "BOJA-",   # Boletín Oficial de la Junta de Andalucía
    "BOPV-",   # Boletín Oficial del País Vasco
    "BOB-",    # Boletín Oficial de Bizkaia
    "BOG-",    # Boletín Oficial de Gipuzkoa
    "BOTHA-",  # Boletín Oficial del Territorio Histórico de Álava
    "BON-",    # Boletín Oficial de Navarra
    "DOG-",    # Diario Oficial de Galicia
    "BOC-",    # Boletín Oficial de Canarias / Cantabria
    "BORM-",   # Boletín Oficial de la Región de Murcia
    "BOIB-",   # Butlletí Oficial de les Illes Balears
    "BOCYL-",  # Boletín Oficial de Castilla y León
    "DOCM-",   # Diario Oficial de Castilla-La Mancha
    "BOPA-",   # Boletín Oficial del Principado de Asturias
    "DOE-",    # Diario Oficial de Extremadura
    "BOR-",    # Boletín Oficial de La Rioja
    "BOA-",    # Boletín Oficial de Aragón
)


def is_regional_bulletin_id(boe_id: str) -> bool:
    """True si `boe_id` empieza por un prefijo de boletín autonómico/foral
    reconocido. Las citas a estos boletines se aceptan como anclaje válido
    aunque no haya hash todavía."""
    return any(boe_id.startswith(prefix) for prefix in REGIONAL_BULLETIN_PREFIXES)


def is_state_bulletin_id(boe_id: str) -> bool:
    """True si `boe_id` apunta al BOE estatal (único boletín con
    verificador SHA-256 activo)."""
    return boe_id.startswith("BOE-A-")
