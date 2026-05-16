"""Tests del contrato de datos (`Source`, `Scope`, `Deduction`)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from hacienda_ai.models import Deduction, ValidationError


def _base_validated_payload() -> dict[str, Any]:
    """Payload mínimo de una deducción `validada` correcta."""
    return {
        "id": "test_validated",
        "name": "Deducción de prueba validada",
        "description": "Regla sintética para tests del contrato.",
        "tax_year": 2025,
        "scope": "estatal",
        "region": None,
        "category": "deduccion",
        "requirements": [{"field": "expenses.test_amount", "operator": ">", "value": 0}],
        "calculation": {"type": "amount_field", "base_field": "expenses.test_amount"},
        "limit": 100.0,
        "taxable_base_limits": {},
        "incompatibilities": [],
        "required_documents": ["Justificante de prueba"],
        "rent_web_boxes": [],
        "sources": [
            {
                "kind": "ley",
                "title": "LIRPF (test)",
                "url": "https://www.boe.es/buscar/act.php?id=BOE-A-2006-20764",
                "article": "art. 1",
                "boe_id": "BOE-A-2006-20764",
                "content_hash": "b" * 64,
                "checked_at": "2026-05-11",
            }
        ],
        "effective_from": "2025-01-01",
        "effective_to": "2025-12-31",
        "last_reviewed_at": "2026-05-11",
        "risk_level": "bajo",
        "validation_status": "validada",
    }


def test_validada_requires_at_least_one_source_with_anchor() -> None:
    payload = _base_validated_payload()
    payload["sources"] = [
        {
            "kind": "ley",
            "title": "Sin boe_id ni hash",
            "url": None,
            "checked_at": "2026-05-11",
        }
    ]
    with pytest.raises(
        ValidationError,
        match="anclada a BOE estatal|boletín autonómico/foral",
    ):
        Deduction.from_dict(payload)


def test_validada_accepts_when_at_least_one_source_has_anchor() -> None:
    payload = _base_validated_payload()
    payload["sources"].append(
        {
            "kind": "dgt_vinculante",
            "title": "DGT V0123-24 (sin hash, complementaria)",
            "url": None,
            "checked_at": "2026-05-11",
        }
    )
    deduction = Deduction.from_dict(payload)
    assert len(deduction.sources) == 2
    assert deduction.sources[0].boe_id == "BOE-A-2006-20764"


def test_source_kind_unknown_raises() -> None:
    payload = _base_validated_payload()
    payload["sources"][0]["kind"] = "boletin_aleatorio"
    with pytest.raises(ValidationError, match="source.kind"):
        Deduction.from_dict(payload)


def test_content_hash_must_be_sha256_hex() -> None:
    payload = _base_validated_payload()
    payload["sources"][0]["content_hash"] = "not-a-real-hash"
    with pytest.raises(ValidationError, match="SHA-256"):
        Deduction.from_dict(payload)


def test_content_hash_normalizes_uppercase_to_lowercase() -> None:
    payload = _base_validated_payload()
    payload["sources"][0]["content_hash"] = "B" * 64
    deduction = Deduction.from_dict(payload)
    assert deduction.sources[0].content_hash == "b" * 64


def test_scope_foral_requires_foral_territory() -> None:
    payload = _base_validated_payload()
    payload["scope"] = "foral"
    # Sin foral_territory ⇒ ValidationError.
    with pytest.raises(ValidationError, match="foral_territory"):
        Deduction.from_dict(payload)


def test_foral_territory_only_allowed_with_scope_foral() -> None:
    payload = _base_validated_payload()
    payload["foral_territory"] = "bizkaia"
    # scope=estatal con foral_territory ⇒ ValidationError.
    with pytest.raises(ValidationError, match="foral_territory.*scope=foral"):
        Deduction.from_dict(payload)


def test_foral_deduction_with_valid_territory_loads() -> None:
    payload = _base_validated_payload()
    payload["scope"] = "foral"
    payload["foral_territory"] = "bizkaia"
    deduction = Deduction.from_dict(payload)
    assert deduction.scope.value == "foral"
    assert deduction.foral_territory is not None
    assert deduction.foral_territory.value == "bizkaia"


def test_foral_territory_unknown_raises() -> None:
    payload = _base_validated_payload()
    payload["scope"] = "foral"
    payload["foral_territory"] = "soria"
    with pytest.raises(ValidationError, match="foral_territory"):
        Deduction.from_dict(payload)


def test_pendiente_fuente_does_not_require_anchor() -> None:
    """Las semillas en `pendiente_fuente` no exigen boe_id ni hash."""
    payload = _base_validated_payload()
    payload["validation_status"] = "pendiente_fuente"
    payload["sources"] = [
        {
            "kind": "pendiente_validacion",
            "title": "Por contrastar",
            "url": None,
            "checked_at": None,
        }
    ]
    deduction = Deduction.from_dict(payload)
    assert deduction.validation_status.value == "pendiente_fuente"
    assert deduction.sources[0].boe_id is None


def test_payload_isolation_not_mutated() -> None:
    """`Deduction.from_dict` no debe mutar el dict de entrada."""
    payload = _base_validated_payload()
    snapshot = copy.deepcopy(payload)
    Deduction.from_dict(payload)
    assert payload == snapshot
