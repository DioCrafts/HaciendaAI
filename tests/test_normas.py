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
    assert (DEFAULT_NORMAS_DIR / "bocm_madrid_irpf.json").is_file()


# --------------------------------------------------------------------------
# Sprint 1 #2: BOCM-2010-258 (Decreto Legislativo 1/2010 CM) entra en el
# registry. Es la única norma autonómica que cita el corpus actual.
# --------------------------------------------------------------------------

BOCM_MADRID_TRTC = "BOCM-2010-258"


def test_registry_knows_bocm_madrid_decreto_legislativo() -> None:
    registry = load_norma_registry()
    assert registry.knows(BOCM_MADRID_TRTC), (
        f"El registry debería incluir {BOCM_MADRID_TRTC} (Decreto Legislativo "
        "1/2010 CM, TRTC tributos cedidos) tras Sprint 1 #2"
    )
    norma = registry.get_norma(BOCM_MADRID_TRTC)
    assert norma is not None
    assert norma.enacted_at == date(2010, 10, 21)


def test_registry_resolves_bocm_madrid_version_for_2024_and_2025() -> None:
    """Las 12 deducciones autonómicas de Madrid tienen `effective_from=2024-01-01`
    y el corpus 2025 las heredará: la ventana abierta del registry tiene
    que cubrir devengos en ambos ejercicios."""
    registry = load_norma_registry()
    v24 = registry.version_at(BOCM_MADRID_TRTC, date(2024, 6, 1))
    v25 = registry.version_at(BOCM_MADRID_TRTC, date(2025, 6, 1))
    assert v24 is not None
    assert v25 is not None
    assert v24.status == NormaStatus.VIGENTE
    assert v25.status == NormaStatus.VIGENTE
    assert v24.effective_from == date(2024, 1, 1)
    assert v24.effective_to is None  # ventana abierta hasta nueva modificación


def test_registry_does_not_invent_history_for_bocm_madrid_pre_2024() -> None:
    """Decisión consciente: la ventana de BOCM-2010-258 arranca en 2024-01-01
    porque el corpus solo cubre devengos 2024+. Consultar la norma en una
    fecha anterior devuelve `None` (no inventamos history). Cuando el
    corpus incorpore deducciones pre-2024, este test debe actualizarse y
    añadirse la ventana correspondiente."""
    registry = load_norma_registry()
    assert registry.version_at(BOCM_MADRID_TRTC, date(2023, 12, 31)) is None
    assert registry.version_at(BOCM_MADRID_TRTC, date(2010, 12, 30)) is None


def test_evaluating_corpus_does_not_emit_unregistered_norma_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cruce con QW1: la WARN sobre norma no registrada solo aparece cuando
    una cita queda fuera del registry. Tras Sprint 1 #2 con BOCM-2010-258
    registrado, evaluar el corpus completo (estatal 2024 + 2025 + Madrid)
    contra un perfil sintético no debe emitir esa WARN ni una sola vez."""
    import logging

    from hacienda_ai.deductions import load_deductions
    from hacienda_ai.models import TaxProfile
    from hacienda_ai.rules import evaluate_deductions

    registry = load_norma_registry()
    deductions = load_deductions()
    profile = TaxProfile.from_dict({
        "tax_year": 2024,
        "region": "Madrid",
        "family": {"children_count": 1},
        "income": {"work_gross": 30000, "work_net": 27500},
        "documents": [],
    })
    with caplog.at_level(logging.WARNING, logger="hacienda_ai.rules"):
        evaluate_deductions(deductions, profile, registry)
    unregistered_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and "no registrada" in r.getMessage()
    ]
    assert unregistered_warnings == [], (
        "WARN inesperadas tras Sprint 1 #2: "
        f"{[r.getMessage() for r in unregistered_warnings]}"
    )


def test_every_validated_corpus_citation_is_registered() -> None:
    """Garantía de cobertura cerrada en Sprint 1 #2: toda norma citada por
    una deducción `validada` está en el `NormaRegistry`, por lo que el
    filtro temporal por vigencia/derogación/inconstitucionalidad se
    aplica a todas las citas (estatales y autonómicas Madrid). Si en el
    futuro se añade una nueva deducción que cite una norma no registrada,
    este test falla y obliga a registrar la norma antes de validarla."""
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
