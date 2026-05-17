"""Tests del checker de freshness del corpus.

Verifica las tres ramas (norma_registry, deductions_review,
jurisprudence_corpus) tanto en su modo fresh como stale, además del
caso SKIPPED para el corpus doctrinal cuando los subdirectorios no
existen.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

from hacienda_ai.models import (
    ConsultaDGT,
    CriterioConfidence,
    Deduction,
    DeductionCategory,
    FalloSentido,
    Impuesto,
    Norma,
    NormaRegistry,
    NormaStatus,
    Organo,
    OrganoTEA,
    RatioConfidence,
    ResolucionTEAC,
    RiskLevel,
    Scope,
    Sentencia,
    SentidoResolucion,
    SourceKind,
    TipoResolucion,
    ValidationStatus,
    VersionNorma,
)

# Cargamos el script directamente desde scripts/ (no es paquete Python).
REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "_check_corpus_freshness",
    REPO_ROOT / "scripts" / "check_corpus_freshness.py",
)
assert _SPEC is not None and _SPEC.loader is not None
freshness = importlib.util.module_from_spec(_SPEC)
sys.modules["_check_corpus_freshness"] = freshness
_SPEC.loader.exec_module(freshness)


# ---------- Fixtures helper ----------


def _make_norma_registry(enacted_dates: list[date]) -> NormaRegistry:
    reg = NormaRegistry()
    for i, d in enumerate(enacted_dates):
        boe_id = f"BOE-A-{d.year}-{1000 + i}"
        reg.register_norma(
            Norma(
                boe_id=boe_id,
                kind=SourceKind.LEY,
                title=f"Norma {i}",
                enacted_at=d,
            )
        )
        reg.register_version(
            VersionNorma(
                norma_boe_id=boe_id,
                effective_from=d,
                effective_to=None,
                status=NormaStatus.VIGENTE,
                modified_by_boe_id=None,
                notes=None,
            )
        )
    return reg


def _make_deduction(
    *,
    id_: str = "ded_test",
    last_reviewed_at: date | None = None,
) -> Deduction:
    return Deduction(
        id=id_,
        name="Test",
        description="Descripción de prueba con longitud suficiente.",
        tax_year=2024,
        scope=Scope.ESTATAL,
        region=None,
        category=DeductionCategory.DEDUCCION,
        requirements=(),
        sources=(),
        calculation=None,
        limit=None,
        taxable_base_limits={},
        incompatibilities=(),
        required_documents=(),
        rent_web_boxes=(),
        risk_level=RiskLevel.BAJO,
        validation_status=ValidationStatus.VALIDADA,
        effective_from=None,
        effective_to=None,
        last_reviewed_at=last_reviewed_at,
        foral_territory=None,
    )


def _write_sentencia(dir_: Path, *, ecli: str, last_fetched: date) -> None:
    s = Sentencia(
        ecli=ecli,
        organo=Organo.TS,
        tribunal_codigo="TS",
        sala="Tercera",
        seccion=None,
        fecha=date(2024, 1, 1),
        ponente=None,
        numero_resolucion=None,
        numero_recurso=None,
        fallo_sentido=FalloSentido.DESESTIMATORIA,
        fallo_texto="Texto.",
        ratio_decidendi=None,
        ratio_confidence=RatioConfidence.AUTO,
        resumen=None,
        url=None,
        content_hash="a" * 64,
        last_fetched_at=last_fetched,
    )
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"{ecli.replace(':', '-')}.json").write_text(
        json.dumps(s.to_dict()),
        encoding="utf-8",
    )


def _write_dgt(dir_: Path, *, numero: str, last_fetched: date) -> None:
    c = ConsultaDGT(
        numero=numero,
        fecha_salida=date(2024, 1, 30),
        fecha_entrada=None,
        impuesto=Impuesto.IRPF,
        asunto="Test",
        cuestion_planteada="...",
        contestacion_completa="...",
        criterio=None,
        criterio_confidence=CriterioConfidence.AUTO,
        normativa=(),
        url=None,
        content_hash="b" * 64,
        last_fetched_at=last_fetched,
    )
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"{numero}.json").write_text(
        json.dumps(c.to_dict()),
        encoding="utf-8",
    )


def _write_teac(dir_: Path, *, numero: str, last_fetched: date) -> None:
    r = ResolucionTEAC(
        numero=numero,
        organo=OrganoTEA.TEAC,
        sede="Madrid",
        fecha=date(2023, 6, 15),
        tipo=TipoResolucion.UNIFICA_CRITERIO,
        sentido=SentidoResolucion.DESESTIMATORIA,
        impuesto=Impuesto.IRPF,
        asunto="Asunto",
        criterio=None,
        criterio_confidence=CriterioConfidence.AUTO,
        normativa=(),
        resolucion_texto="...",
        url=None,
        content_hash="c" * 64,
        last_fetched_at=last_fetched,
    )
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"{numero.replace('/', '_')}.json").write_text(
        json.dumps(r.to_dict()),
        encoding="utf-8",
    )


# ---------- check_norma_freshness ----------


def test_norma_fresh_when_recent_enacted_at() -> None:
    reg = _make_norma_registry([date(2026, 5, 1)])
    result = freshness.check_norma_freshness(
        reg, today=date(2026, 5, 17), max_age_days=30
    )
    assert result.is_fresh is True
    assert result.latest_age_days == 16
    assert result.latest_item_id == "BOE-A-2026-1000"


def test_norma_stale_when_old() -> None:
    reg = _make_norma_registry([date(2024, 1, 1)])
    result = freshness.check_norma_freshness(
        reg, today=date(2026, 5, 17), max_age_days=30
    )
    assert result.is_fresh is False
    assert result.latest_age_days is not None
    assert result.latest_age_days > 30


def test_norma_picks_most_recent_across_multiple() -> None:
    reg = _make_norma_registry(
        [date(2020, 1, 1), date(2026, 5, 10), date(2025, 6, 1)]
    )
    result = freshness.check_norma_freshness(
        reg, today=date(2026, 5, 17), max_age_days=30
    )
    assert result.is_fresh is True
    assert result.latest_item_date == "2026-05-10"


def test_norma_empty_registry_is_stale() -> None:
    """Registry vacío = nada que verificar = stale (no se puede afirmar
    freshness sin evidencia). El cron debería abrir issue."""
    reg = NormaRegistry()
    result = freshness.check_norma_freshness(
        reg, today=date(2026, 5, 17), max_age_days=30
    )
    assert result.is_fresh is False
    assert result.latest_item_id is None
    assert result.latest_age_days is None


# ---------- check_deductions_freshness ----------


def test_deductions_fresh_when_recent_review() -> None:
    deds = [
        _make_deduction(id_="a", last_reviewed_at=date(2025, 12, 1)),
        _make_deduction(id_="b", last_reviewed_at=date(2026, 5, 10)),
    ]
    result = freshness.check_deductions_freshness(
        deds, today=date(2026, 5, 17), max_age_days=180
    )
    assert result.is_fresh is True
    assert result.latest_item_id == "b"


def test_deductions_stale_when_all_old() -> None:
    deds = [_make_deduction(last_reviewed_at=date(2023, 1, 1))]
    result = freshness.check_deductions_freshness(
        deds, today=date(2026, 5, 17), max_age_days=180
    )
    assert result.is_fresh is False


def test_deductions_stale_when_none_have_review_date() -> None:
    """Si NINGUNA deducción tiene last_reviewed_at: stale. Es señal de
    que la curaduría no se está marcando."""
    deds = [_make_deduction(last_reviewed_at=None)]
    result = freshness.check_deductions_freshness(
        deds, today=date(2026, 5, 17), max_age_days=180
    )
    assert result.is_fresh is False
    assert result.latest_item_id is None


# ---------- check_jurisprudence_freshness ----------


def test_jurisprudence_skipped_when_subdirs_missing(tmp_path: Path) -> None:
    result = freshness.check_jurisprudence_freshness(
        tmp_path, today=date(2026, 5, 17), max_age_days=90
    )
    assert result.skipped is True
    assert result.is_fresh is True
    assert result.skip_reason is not None


def test_jurisprudence_fresh_when_recent_fetch(tmp_path: Path) -> None:
    _write_sentencia(
        tmp_path / "jurisprudencia" / "ts" / "2024",
        ecli="ECLI:ES:TS:2024:1234",
        last_fetched=date(2026, 5, 1),
    )
    result = freshness.check_jurisprudence_freshness(
        tmp_path, today=date(2026, 5, 17), max_age_days=90
    )
    assert result.skipped is False
    assert result.is_fresh is True
    assert result.latest_item_id == "ECLI:ES:TS:2024:1234"


def test_jurisprudence_stale_when_all_old(tmp_path: Path) -> None:
    _write_sentencia(
        tmp_path / "jurisprudencia" / "ts" / "2024",
        ecli="ECLI:ES:TS:2024:1234",
        last_fetched=date(2024, 1, 1),
    )
    result = freshness.check_jurisprudence_freshness(
        tmp_path, today=date(2026, 5, 17), max_age_days=90
    )
    assert result.is_fresh is False


def test_jurisprudence_aggregates_across_three_families(tmp_path: Path) -> None:
    """El check debe inspeccionar jurisprudencia/, dgt_consultas/ y
    teac_resoluciones/ y devolver el item MÁS reciente de los tres."""
    _write_sentencia(
        tmp_path / "jurisprudencia" / "ts" / "2024",
        ecli="ECLI:ES:TS:2024:1234",
        last_fetched=date(2024, 1, 1),  # antiguo
    )
    _write_dgt(
        tmp_path / "dgt_consultas" / "2024",
        numero="V0123-24",
        last_fetched=date(2026, 5, 10),  # reciente — gana
    )
    _write_teac(
        tmp_path / "teac_resoluciones" / "teac" / "2023",
        numero="00/12345/2023",
        last_fetched=date(2025, 1, 1),
    )
    result = freshness.check_jurisprudence_freshness(
        tmp_path, today=date(2026, 5, 17), max_age_days=90
    )
    assert result.is_fresh is True
    assert result.latest_item_id == "V0123-24"
    assert result.latest_item_date == "2026-05-10"


def test_jurisprudence_existing_dir_but_empty_is_stale(tmp_path: Path) -> None:
    """Subdirectorio existe pero sin items: stale (alguien creó la
    estructura y no la pobló)."""
    (tmp_path / "jurisprudencia").mkdir()
    result = freshness.check_jurisprudence_freshness(
        tmp_path, today=date(2026, 5, 17), max_age_days=90
    )
    assert result.is_fresh is False
    assert result.skipped is False


# ---------- build_report ----------


def test_build_report_aggregates_to_fresh_when_all_fresh(tmp_path: Path) -> None:
    reg = _make_norma_registry([date(2026, 5, 10)])
    deds = [_make_deduction(last_reviewed_at=date(2026, 5, 10))]
    report = freshness.build_report(
        today=date(2026, 5, 17),
        registry=reg,
        deductions=deds,
        data_dir=tmp_path,
        max_boe_age_days=30,
        max_deduction_review_age_days=180,
        max_jurisprudence_age_days=90,
    )
    assert report.is_fresh is True
    assert all(c.is_fresh for c in report.checks)


def test_build_report_is_stale_when_any_check_fails(tmp_path: Path) -> None:
    reg = _make_norma_registry([date(2020, 1, 1)])  # muy antiguo
    deds = [_make_deduction(last_reviewed_at=date(2026, 5, 10))]
    report = freshness.build_report(
        today=date(2026, 5, 17),
        registry=reg,
        deductions=deds,
        data_dir=tmp_path,
        max_boe_age_days=30,
        max_deduction_review_age_days=180,
        max_jurisprudence_age_days=90,
    )
    assert report.is_fresh is False
    assert len(report.stale_checks) == 1
    assert report.stale_checks[0].name == "norma_registry"


def test_to_dict_is_json_serializable(tmp_path: Path) -> None:
    reg = _make_norma_registry([date(2026, 5, 10)])
    deds = [_make_deduction(last_reviewed_at=date(2026, 5, 10))]
    report = freshness.build_report(
        today=date(2026, 5, 17),
        registry=reg,
        deductions=deds,
        data_dir=tmp_path,
        max_boe_age_days=30,
        max_deduction_review_age_days=180,
        max_jurisprudence_age_days=90,
    )
    serialized = json.dumps(report.to_dict())
    parsed = json.loads(serialized)
    assert parsed["is_fresh"] is True
    assert len(parsed["checks"]) == 3


# ---------- main CLI ----------


def test_main_exits_0_when_fresh(tmp_path: Path, monkeypatch) -> None:
    # Forzamos data_dir tmp y umbrales gigantes → siempre fresh.
    report_path = tmp_path / "report.json"
    rc = freshness.main(
        [
            "--data-dir",
            str(tmp_path),
            "--max-boe-age-days",
            "10000",
            "--max-deduction-review-age-days",
            "10000",
            "--max-jurisprudence-age-days",
            "10000",
            "--today",
            "2026-05-17",
            "--report",
            str(report_path),
        ]
    )
    assert rc == 0
    payload = json.loads(report_path.read_text())
    assert payload["is_fresh"] is True


def test_main_exits_1_when_stale(tmp_path: Path) -> None:
    # Umbral 0 → cualquier item > 0 días = stale.
    rc = freshness.main(
        [
            "--data-dir",
            str(tmp_path),
            "--max-boe-age-days",
            "0",
            "--max-deduction-review-age-days",
            "0",
            "--max-jurisprudence-age-days",
            "0",
            "--today",
            "2026-05-17",
        ]
    )
    assert rc == 1


def test_main_exits_2_on_invalid_max(tmp_path: Path) -> None:
    rc = freshness.main(
        [
            "--data-dir",
            str(tmp_path),
            "--max-boe-age-days",
            "-1",
            "--today",
            "2026-05-17",
        ]
    )
    assert rc == 2


def test_main_exits_2_on_invalid_today(tmp_path: Path) -> None:
    rc = freshness.main(
        [
            "--data-dir",
            str(tmp_path),
            "--today",
            "not-a-date",
        ]
    )
    assert rc == 2


def test_render_summary_human_readable() -> None:
    reg = _make_norma_registry([date(2026, 5, 10)])
    deds = [_make_deduction(last_reviewed_at=date(2026, 5, 10))]
    report = freshness.build_report(
        today=date(2026, 5, 17),
        registry=reg,
        deductions=deds,
        data_dir=Path("/nonexistent"),
        max_boe_age_days=30,
        max_deduction_review_age_days=180,
        max_jurisprudence_age_days=90,
    )
    summary = freshness.render_summary(report)
    assert "FRESH" in summary
    assert "norma_registry" in summary
    assert "SKIPPED" in summary  # jurisprudence: nonexistent dir
