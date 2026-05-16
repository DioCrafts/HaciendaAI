"""Tests de `Norma`, `VersionNorma`, `NormaRegistry` y filtro temporal."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import pytest

from hacienda_ai.models import (
    Deduction,
    Norma,
    NormaRegistry,
    NormaStatus,
    SourceKind,
    TaxProfile,
    ValidationError,
    VersionNorma,
)
from hacienda_ai.rules import evaluate_deduction

# ----------------------------- helpers ---------------------------------------


def _lirpf_norma() -> Norma:
    return Norma(
        boe_id="BOE-A-2006-20764",
        kind=SourceKind.LEY,
        title="Ley 35/2006 del IRPF",
        enacted_at=date(2006, 11, 28),
    )


def _validated_payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "test_validada",
        "name": "Deducción validada de prueba",
        "description": "Regla sintética para tests de norma y vigencia.",
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
                "title": "LIRPF",
                "url": "https://www.boe.es/buscar/act.php?id=BOE-A-2006-20764",
                "article": "art. 1",
                "boe_id": "BOE-A-2006-20764",
                "content_hash": "a" * 64,
                "checked_at": "2026-05-11",
            }
        ],
        "effective_from": "2025-01-01",
        "effective_to": "2025-12-31",
        "last_reviewed_at": "2026-05-11",
        "risk_level": "bajo",
        "validation_status": "validada",
    }
    data.update(overrides)
    return data


def _profile(**overrides: Any) -> TaxProfile:
    data: dict[str, Any] = {
        "tax_year": 2025,
        "region": "Madrid",
        "expenses": {"test_amount": 120.0},
        "documents": ["Justificante de prueba"],
    }
    data.update(overrides)
    return TaxProfile.from_dict(data)


# ----------------------------- VersionNorma ---------------------------------


def test_version_norma_covers_date_inside_range() -> None:
    v = VersionNorma(
        norma_boe_id="BOE-A-2006-20764",
        effective_from=date(2025, 1, 1),
        effective_to=date(2025, 12, 31),
        status=NormaStatus.VIGENTE,
    )
    assert v.covers(date(2025, 6, 1))
    assert v.is_active_on(date(2025, 6, 1))


def test_version_norma_does_not_cover_outside_range() -> None:
    v = VersionNorma(
        norma_boe_id="BOE-A-2006-20764",
        effective_from=date(2025, 1, 1),
        effective_to=date(2025, 12, 31),
        status=NormaStatus.VIGENTE,
    )
    assert not v.covers(date(2024, 12, 31))
    assert not v.covers(date(2026, 1, 1))


def test_version_norma_open_ended_covers_future() -> None:
    v = VersionNorma(
        norma_boe_id="BOE-A-2006-20764",
        effective_from=date(2025, 1, 1),
        status=NormaStatus.VIGENTE,
    )
    assert v.covers(date(2099, 12, 31))


def test_version_norma_derogada_covers_but_not_active() -> None:
    v = VersionNorma(
        norma_boe_id="BOE-A-2006-20764",
        effective_from=date(2025, 1, 1),
        effective_to=date(2025, 6, 30),
        status=NormaStatus.DEROGADA,
    )
    assert v.covers(date(2025, 3, 1))
    assert not v.is_active_on(date(2025, 3, 1))


def test_version_norma_rejects_inverted_dates() -> None:
    with pytest.raises(ValidationError, match="anterior a effective_from"):
        VersionNorma(
            norma_boe_id="BOE-A-2006-20764",
            effective_from=date(2025, 12, 31),
            effective_to=date(2025, 1, 1),
            status=NormaStatus.VIGENTE,
        )


# ----------------------------- NormaRegistry --------------------------------


def test_registry_registers_and_looks_up() -> None:
    reg = NormaRegistry()
    reg.register_norma(_lirpf_norma())
    reg.register_version(
        VersionNorma(
            norma_boe_id="BOE-A-2006-20764",
            effective_from=date(2025, 1, 1),
            effective_to=None,
            status=NormaStatus.VIGENTE,
        )
    )
    assert reg.knows("BOE-A-2006-20764")
    assert reg.status_at("BOE-A-2006-20764", date(2025, 6, 1)) == NormaStatus.VIGENTE


def test_registry_rejects_overlapping_versions() -> None:
    reg = NormaRegistry()
    reg.register_norma(_lirpf_norma())
    reg.register_version(
        VersionNorma(
            norma_boe_id="BOE-A-2006-20764",
            effective_from=date(2020, 1, 1),
            effective_to=date(2025, 12, 31),
            status=NormaStatus.VIGENTE,
        )
    )
    with pytest.raises(ValidationError, match="Solapamiento"):
        reg.register_version(
            VersionNorma(
                norma_boe_id="BOE-A-2006-20764",
                effective_from=date(2025, 6, 1),
                effective_to=date(2026, 12, 31),
                status=NormaStatus.VIGENTE,
            )
        )


def test_registry_allows_adjacent_versions() -> None:
    reg = NormaRegistry()
    reg.register_norma(_lirpf_norma())
    reg.register_version(
        VersionNorma(
            norma_boe_id="BOE-A-2006-20764",
            effective_from=date(2023, 1, 1),
            effective_to=date(2024, 12, 31),
            status=NormaStatus.VIGENTE,
        )
    )
    reg.register_version(
        VersionNorma(
            norma_boe_id="BOE-A-2006-20764",
            effective_from=date(2025, 1, 1),
            effective_to=None,
            status=NormaStatus.VIGENTE,
        )
    )
    assert reg.status_at("BOE-A-2006-20764", date(2024, 6, 1)) == NormaStatus.VIGENTE
    assert reg.status_at("BOE-A-2006-20764", date(2025, 6, 1)) == NormaStatus.VIGENTE


def test_registry_rejects_duplicate_norma_with_different_metadata() -> None:
    reg = NormaRegistry()
    reg.register_norma(_lirpf_norma())
    with pytest.raises(ValidationError, match="metadatos distintos"):
        reg.register_norma(
            Norma(
                boe_id="BOE-A-2006-20764",
                kind=SourceKind.LEY,
                title="Título distinto",
                enacted_at=date(2006, 11, 28),
            )
        )


def test_registry_from_dict_loads_normas_and_versions() -> None:
    data = {
        "normas": [
            {
                "boe_id": "BOE-A-2006-20764",
                "kind": "ley",
                "title": "LIRPF",
                "enacted_at": "2006-11-28",
            }
        ],
        "versions": [
            {
                "norma_boe_id": "BOE-A-2006-20764",
                "effective_from": "2025-01-01",
                "effective_to": None,
                "status": "vigente",
            }
        ],
    }
    reg = NormaRegistry.from_dict(data)
    assert reg.knows("BOE-A-2006-20764")
    assert reg.status_at("BOE-A-2006-20764", date(2025, 12, 31)) == NormaStatus.VIGENTE


def test_registry_from_dict_rejects_version_for_unknown_norma() -> None:
    data = {
        "normas": [],
        "versions": [
            {
                "norma_boe_id": "BOE-A-2006-20764",
                "effective_from": "2025-01-01",
                "status": "vigente",
            }
        ],
    }
    with pytest.raises(ValidationError, match="no registrada"):
        NormaRegistry.from_dict(data)


# ------------------- Filtro temporal en evaluate_deduction -------------------


def test_devengo_before_effective_from_blocks_application() -> None:
    deduction = Deduction.from_dict(
        _validated_payload(effective_from="2025-06-01", effective_to="2025-12-31")
    )
    profile = _profile(devengo_date="2025-03-15")
    result = evaluate_deduction(deduction, profile)
    assert result.status == "does_not_apply"
    assert "no estaba en vigor" in result.reason
    assert result.confidence == 0.95


def test_devengo_after_effective_to_blocks_application() -> None:
    deduction = Deduction.from_dict(
        _validated_payload(effective_from="2025-01-01", effective_to="2025-06-30")
    )
    profile = _profile(devengo_date="2025-09-15")
    result = evaluate_deduction(deduction, profile)
    assert result.status == "does_not_apply"
    assert "dejó de estar en vigor" in result.reason


def test_devengo_within_vigencia_passes_temporal_check() -> None:
    deduction = Deduction.from_dict(_validated_payload())
    result = evaluate_deduction(deduction, _profile())
    assert result.status == "applies"


def test_devengo_date_must_match_tax_year() -> None:
    with pytest.raises(ValidationError, match="debe pertenecer al tax_year"):
        TaxProfile.from_dict({"tax_year": 2025, "region": "Madrid", "devengo_date": "2024-12-31"})


def test_devengo_defaults_to_dec_31_when_not_provided() -> None:
    profile = TaxProfile.from_dict({"tax_year": 2025, "region": "Madrid"})
    assert profile.effective_devengo_date() == date(2025, 12, 31)


# ----------- Filtro de estado de norma vía NormaRegistry ---------------------


def _registry_with_status(status: NormaStatus, effective_to: date | None = None) -> NormaRegistry:
    reg = NormaRegistry()
    reg.register_norma(_lirpf_norma())
    reg.register_version(
        VersionNorma(
            norma_boe_id="BOE-A-2006-20764",
            effective_from=date(2025, 1, 1),
            effective_to=effective_to,
            status=status,
        )
    )
    return reg


def test_derogated_norma_blocks_even_if_requirements_met() -> None:
    deduction = Deduction.from_dict(_validated_payload())
    result = evaluate_deduction(
        deduction, _profile(), registry=_registry_with_status(NormaStatus.DEROGADA)
    )
    assert result.status == "does_not_apply"
    assert "derogada" in result.reason
    assert result.confidence == 0.95


def test_inconstitucional_norma_blocks_application() -> None:
    deduction = Deduction.from_dict(_validated_payload())
    result = evaluate_deduction(
        deduction, _profile(), registry=_registry_with_status(NormaStatus.INCONSTITUCIONAL)
    )
    assert result.status == "does_not_apply"
    assert "inconstitucional" in result.reason


def test_suspendida_norma_degrades_to_pending_validation() -> None:
    deduction = Deduction.from_dict(_validated_payload())
    result = evaluate_deduction(
        deduction, _profile(), registry=_registry_with_status(NormaStatus.SUSPENDIDA)
    )
    assert result.status == "pending_validation"
    assert "suspendida" in result.reason


def test_vigente_norma_does_not_block() -> None:
    deduction = Deduction.from_dict(_validated_payload())
    result = evaluate_deduction(
        deduction, _profile(), registry=_registry_with_status(NormaStatus.VIGENTE)
    )
    assert result.status == "applies"


def test_norma_outside_devengo_window_degrades_to_pending_validation() -> None:
    reg = NormaRegistry()
    reg.register_norma(_lirpf_norma())
    reg.register_version(
        VersionNorma(
            norma_boe_id="BOE-A-2006-20764",
            effective_from=date(2020, 1, 1),
            effective_to=date(2024, 12, 31),
            status=NormaStatus.VIGENTE,
        )
    )
    deduction = Deduction.from_dict(_validated_payload())
    result = evaluate_deduction(deduction, _profile(), registry=reg)
    assert result.status == "pending_validation"
    assert "No consta versión" in result.reason


def test_unknown_norma_in_registry_does_not_block_but_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Una norma citada y no registrada no rompe la evaluación, pero deja un
    WARN en logs: el filtro de vigencia no se puede aplicar y es un agujero
    de garantía que el operador debe ver."""
    reg = NormaRegistry()
    deduction = Deduction.from_dict(_validated_payload())
    with caplog.at_level(logging.WARNING, logger="hacienda_ai.rules"):
        result = evaluate_deduction(deduction, _profile(), registry=reg)
    assert result.status == "applies"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "no registrada" in r.getMessage() and "BOE-A-2006-20764" in r.getMessage()
        for r in warnings
    ), f"esperado WARN sobre norma no registrada; recibido: {[r.getMessage() for r in warnings]}"


def test_source_without_boe_id_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Una fuente legítimamente sin `boe_id` (p. ej. `pendiente_validacion`)
    NO debe ensuciar logs con WARN: solo la cita que apunta a una norma con
    `boe_id` desconocido cuenta como agujero de garantía."""
    payload = _validated_payload()
    payload["sources"] = [
        {
            "kind": "pendiente_validacion",
            "title": "Sin fuente formal todavía",
            "url": None,
            "article": None,
            "paragraph": None,
            "boe_id": None,
            "content_hash": None,
            "checked_at": None,
        }
    ]
    payload["validation_status"] = "pendiente_fuente"
    deduction = Deduction.from_dict(payload)
    reg = NormaRegistry()
    with caplog.at_level(logging.WARNING, logger="hacienda_ai.rules"):
        evaluate_deduction(deduction, _profile(), registry=reg)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [], (
        f"no debería haber WARN para boe_id=None; recibido: "
        f"{[r.getMessage() for r in warnings]}"
    )


# ----------- Vigencia con effective_to/from invertidos ----------------------


def test_deduction_rejects_inverted_effective_dates() -> None:
    with pytest.raises(ValidationError, match="anterior a effective_from"):
        Deduction.from_dict(
            _validated_payload(effective_from="2025-12-31", effective_to="2025-01-01")
        )


def test_invalid_iso_date_in_effective_from_raises() -> None:
    with pytest.raises(ValidationError, match="ISO 8601"):
        Deduction.from_dict(_validated_payload(effective_from="2025/01/01"))
