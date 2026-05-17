"""Test de integración del pipeline TEAC/TEAR."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from hacienda_ai.models import (
    CriterioConfidence,
    Impuesto,
    OrganoTEA,
    SentidoResolucion,
    TipoResolucion,
)
from hacienda_ai.rag.teac import (
    LocalTeacClient,
    impuesto_breakdown,
    load_resolucion,
    run_ingest_for_numeros,
    tipo_breakdown,
)

FIXTURES = Path(__file__).parent / "fixtures" / "teac"


def test_pipeline_procesa_las_tres_resoluciones(tmp_path: Path) -> None:
    client = LocalTeacClient(root_dir=FIXTURES)
    report = run_ingest_for_numeros(
        ["00/12345/2023", "00/67890/2022", "28/00345/2024"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert len(report.accepted) == 3
    assert len(report.errored) == 0


def test_pipeline_detecta_organo_y_tipo_correctos(tmp_path: Path) -> None:
    client = LocalTeacClient(root_dir=FIXTURES)
    report = run_ingest_for_numeros(
        ["00/12345/2023", "00/67890/2022", "28/00345/2024"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    by_numero = {
        o.resolucion.numero: o.resolucion
        for o in report.accepted
        if o.resolucion is not None
    }
    # TEAC unifica criterio.
    r1 = by_numero["00/12345/2023"]
    assert r1.organo == OrganoTEA.TEAC
    assert r1.tipo == TipoResolucion.UNIFICA_CRITERIO
    assert r1.sentido == SentidoResolucion.DESESTIMATORIA
    assert r1.impuesto == Impuesto.IRPF

    # TEAC ordinaria.
    r2 = by_numero["00/67890/2022"]
    assert r2.organo == OrganoTEA.TEAC
    assert r2.tipo == TipoResolucion.ORDINARIA
    assert r2.sentido == SentidoResolucion.ESTIMATORIA_PARCIAL
    assert r2.impuesto == Impuesto.IVA

    # TEAR Madrid.
    r3 = by_numero["28/00345/2024"]
    assert r3.organo == OrganoTEA.TEAR
    assert r3.tipo == TipoResolucion.ORDINARIA
    assert r3.sentido == SentidoResolucion.DESESTIMATORIA
    assert r3.impuesto == Impuesto.ISD


def test_pipeline_persiste_particionado_por_organo_anyo(tmp_path: Path) -> None:
    client = LocalTeacClient(root_dir=FIXTURES)
    run_ingest_for_numeros(
        ["00/12345/2023", "28/00345/2024"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    # Estructura: <organo>/<año>/<numero_safe>.json
    teac_path = tmp_path / "teac" / "2023" / "00_12345_2023.json"
    tear_path = tmp_path / "tear" / "2024" / "28_00345_2024.json"
    assert teac_path.exists()
    assert tear_path.exists()

    r = load_resolucion(teac_path)
    assert r.numero == "00/12345/2023"
    assert r.criterio_confidence == CriterioConfidence.AUTO
    assert r.criterio is not None


def test_pipeline_idempotente_no_reescribe(tmp_path: Path) -> None:
    client = LocalTeacClient(root_dir=FIXTURES)
    r1 = run_ingest_for_numeros(
        ["00/12345/2023"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert r1.newly_persisted[0].persisted is not None
    assert r1.newly_persisted[0].persisted.was_new

    r2 = run_ingest_for_numeros(
        ["00/12345/2023"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert r2.newly_persisted == []
    assert r2.accepted[0].persisted is not None
    assert not r2.accepted[0].persisted.was_new


def test_pipeline_numero_no_encontrado_va_a_errored(tmp_path: Path) -> None:
    client = LocalTeacClient(root_dir=FIXTURES)
    report = run_ingest_for_numeros(
        ["00/12345/2023", "00/99999/2099"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert len(report.accepted) == 1
    assert len(report.errored) == 1


def test_pipeline_numero_invalido_va_a_errored(tmp_path: Path) -> None:
    client = LocalTeacClient(root_dir=FIXTURES)
    report = run_ingest_for_numeros(
        ["esto-no-es-un-numero"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert len(report.errored) == 1
    assert report.errored[0].error is not None
    assert "número inválido" in report.errored[0].error


def test_pipeline_acepta_forma_corta_RG(tmp_path: Path) -> None:
    """`R.G. 12345/2023` debe resolverse al fichero del TEAC central."""
    client = LocalTeacClient(root_dir=FIXTURES)
    report = run_ingest_for_numeros(
        ["R.G. 12345/2023"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert len(report.accepted) == 1
    assert report.accepted[0].resolucion is not None
    assert report.accepted[0].resolucion.numero == "00/12345/2023"


def test_pipeline_dry_run_no_escribe(tmp_path: Path) -> None:
    client = LocalTeacClient(root_dir=FIXTURES)
    report = run_ingest_for_numeros(
        ["00/12345/2023"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
        persist=False,
    )
    assert len(report.accepted) == 1
    assert report.newly_persisted == []
    assert not any(tmp_path.rglob("*.json"))


def test_tipo_breakdown_cuenta_correctamente(tmp_path: Path) -> None:
    client = LocalTeacClient(root_dir=FIXTURES)
    report = run_ingest_for_numeros(
        ["00/12345/2023", "00/67890/2022", "28/00345/2024"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    counts = tipo_breakdown(report)
    assert counts["unifica_criterio"] == 1
    assert counts["ordinaria"] == 2
    assert counts["extiende_efectos"] == 0


def test_impuesto_breakdown_cuenta_por_impuesto(tmp_path: Path) -> None:
    client = LocalTeacClient(root_dir=FIXTURES)
    report = run_ingest_for_numeros(
        ["00/12345/2023", "00/67890/2022", "28/00345/2024"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    counts = impuesto_breakdown(report)
    assert counts["irpf"] == 1
    assert counts["iva"] == 1
    assert counts["isd"] == 1


def test_pipeline_promocion_manual_no_se_pisa(tmp_path: Path) -> None:
    """Edición humana de `criterio_confidence` a manual no debe sobrescribirse."""
    import json

    client = LocalTeacClient(root_dir=FIXTURES)
    run_ingest_for_numeros(
        ["00/12345/2023"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    path = tmp_path / "teac" / "2023" / "00_12345_2023.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["criterio_confidence"] = "manual"
    data["criterio"] = "Criterio validado por revisor humano."
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    run_ingest_for_numeros(
        ["00/12345/2023"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    final = load_resolucion(path)
    assert final.criterio_confidence == CriterioConfidence.MANUAL
    assert final.criterio == "Criterio validado por revisor humano."
