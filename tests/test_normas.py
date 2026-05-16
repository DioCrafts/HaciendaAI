"""Tests del loader `load_norma_registry` y del corpus seed LIRPF."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import NormaRegistry, NormaStatus, ValidationError
from hacienda_ai.normas import DEFAULT_NORMAS_DIR, load_norma_registry

LIRPF = "BOE-A-2006-20764"


def test_load_seed_registry_knows_lirpf() -> None:
    registry = load_norma_registry()
    assert registry.knows(LIRPF), (
        f"El corpus seed debería registrar la LIRPF ({LIRPF})"
    )


def test_load_seed_registry_resolves_three_historical_windows() -> None:
    """El seed cubre 3 ventanas sin solapamientos:
    redacción original (2007–2014), reforma Ley 26/2014 (2015–2021),
    redacción vigente (desde 2022)."""
    registry = load_norma_registry()
    v_original = registry.version_at(LIRPF, date(2010, 6, 30))
    v_reforma = registry.version_at(LIRPF, date(2018, 6, 30))
    v_actual = registry.version_at(LIRPF, date(2024, 12, 31))
    assert v_original is not None
    assert v_reforma is not None
    assert v_actual is not None
    assert v_original.effective_from == date(2007, 1, 1)
    assert v_original.effective_to == date(2014, 12, 31)
    assert v_original.modified_by_boe_id is None
    assert v_reforma.effective_from == date(2015, 1, 1)
    assert v_reforma.modified_by_boe_id == "BOE-A-2014-12328"
    assert v_actual.effective_from == date(2022, 1, 1)
    assert v_actual.effective_to is None
    assert v_actual.modified_by_boe_id == "BOE-A-2021-21657"
    assert v_actual.status == NormaStatus.VIGENTE


def test_load_seed_registry_returns_none_before_law_enactment() -> None:
    """Para fechas anteriores a 2007-01-01 (entrada en vigor) no hay versión."""
    registry = load_norma_registry()
    assert registry.version_at(LIRPF, date(2006, 11, 1)) is None


def test_load_seed_registry_is_a_norma_registry_instance() -> None:
    """El loader devuelve la clase pública, no un proxy."""
    assert isinstance(load_norma_registry(), NormaRegistry)


def test_load_rejects_non_object_root(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(ValidationError, match="objeto"):
        load_norma_registry(tmp_path)


def test_load_rejects_non_list_normas(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"normas": "x", "versions": []}', encoding="utf-8")
    with pytest.raises(ValidationError, match="listas"):
        load_norma_registry(tmp_path)


def test_load_rejects_version_referencing_unknown_norma(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(
        '{"normas": [],'
        ' "versions": [{"norma_boe_id": "BOE-A-2099-1",'
        ' "effective_from": "2024-01-01", "status": "vigente"}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="no registrada"):
        load_norma_registry(tmp_path)


def test_load_concatenates_multiple_files(tmp_path: Path) -> None:
    """Cargar varios archivos JSON combina sus normas y versiones; un archivo
    posterior puede añadir versiones a una norma declarada en uno anterior."""
    norma_file = tmp_path / "01_norma.json"
    norma_file.write_text(
        '{"normas": [{"boe_id": "BOE-A-2006-20764", "kind": "ley",'
        ' "title": "LIRPF", "enacted_at": "2006-11-28"}], "versions": []}',
        encoding="utf-8",
    )
    versions_file = tmp_path / "02_versions.json"
    versions_file.write_text(
        '{"normas": [], "versions": [{"norma_boe_id": "BOE-A-2006-20764",'
        ' "effective_from": "2024-01-01", "status": "vigente"}]}',
        encoding="utf-8",
    )
    registry = load_norma_registry(tmp_path)
    assert registry.knows("BOE-A-2006-20764")
    v = registry.version_at("BOE-A-2006-20764", date(2024, 6, 30))
    assert v is not None
    assert v.status == NormaStatus.VIGENTE


def test_default_normas_dir_is_package_local() -> None:
    """El path por defecto vive dentro del paquete instalado para que
    `pip install -e ".[api]"` sirva el corpus sin pasos adicionales."""
    assert DEFAULT_NORMAS_DIR.is_dir()
    assert (DEFAULT_NORMAS_DIR / "lirpf_versions.json").is_file()


@pytest.mark.xfail(
    strict=False,
    reason=(
        "Gap conocido del Sprint 1: el NormaRegistry semilla solo contiene "
        "LIRPF (BOE-A-2006-20764). El corpus autonómico de Madrid cita "
        "BOCM-2010-258 y aún no hay versionado registrado para esa norma, "
        "por lo que el filtro temporal por estado de norma no se puede "
        "aplicar a esas deducciones. El motor lo señala en WARN; cuando se "
        "amplíe el registry con BOCM, RIRPF y Ley 49/2002, este xfail "
        "pasará a XPASS y debe convertirse en aserción estricta."
    ),
)
def test_every_validated_corpus_citation_is_registered() -> None:
    """Garantía de cobertura: toda norma citada por una deducción `validada`
    debería estar en el `NormaRegistry` para que el filtro temporal por
    vigencia/derogación/inconstitucionalidad pueda aplicarse. Mientras este
    test sea xfail, hay deducciones validadas cuyo filtro de norma está
    desactivado de facto."""
    registry = load_norma_registry()
    deductions = load_deductions()
    missing: dict[str, list[str]] = {}
    for d in deductions:
        if d.validation_status.value != "validada":
            continue
        for s in d.sources:
            if s.boe_id is None or registry.knows(s.boe_id):
                continue
            missing.setdefault(s.boe_id, []).append(d.id)
    assert not missing, (
        "Normas citadas por deducciones validadas que no están en el "
        f"NormaRegistry: {sorted(missing.keys())}. Ejemplos por norma: "
        + "; ".join(
            f"{boe_id} → {ids[:3]}{'…' if len(ids) > 3 else ''}"
            for boe_id, ids in sorted(missing.items())
        )
    )
