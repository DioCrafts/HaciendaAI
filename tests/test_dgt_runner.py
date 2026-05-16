"""Test de integración del pipeline DGT.

Usa `LocalDgtClient` apuntando a los fixtures: procesa los 3 números,
los acepta todos (todos son tributarios), los persiste particionados
por año, y verifica idempotencia + protección de ediciones humanas.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from hacienda_ai.models import (
    ConsultaDGT,
    CriterioConfidence,
    Impuesto,
)
from hacienda_ai.rag.dgt import (
    LocalDgtClient,
    impuesto_breakdown,
    load_consulta,
    run_ingest_for_numeros,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dgt"


def test_pipeline_acepta_las_tres_consultas(tmp_path: Path) -> None:
    client = LocalDgtClient(root_dir=FIXTURES)
    report = run_ingest_for_numeros(
        ["V0123-24", "V0456-24", "V0789-24"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert len(report.accepted) == 3
    assert len(report.errored) == 0
    impuestos = {o.consulta.impuesto for o in report.accepted if o.consulta}
    assert impuestos == {Impuesto.IRPF, Impuesto.IVA, Impuesto.IS}


def test_pipeline_persiste_particionado_por_anyo(tmp_path: Path) -> None:
    client = LocalDgtClient(root_dir=FIXTURES)
    run_ingest_for_numeros(
        ["V0123-24"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    path = tmp_path / "2024" / "V0123-24.json"
    assert path.exists()

    consulta = load_consulta(path)
    assert isinstance(consulta, ConsultaDGT)
    assert consulta.numero == "V0123-24"
    assert consulta.impuesto == Impuesto.IRPF
    assert consulta.criterio_confidence == CriterioConfidence.AUTO
    assert consulta.criterio is not None
    assert len(consulta.normativa) > 0


def test_pipeline_idempotente_no_reescribe(tmp_path: Path) -> None:
    client = LocalDgtClient(root_dir=FIXTURES)
    r1 = run_ingest_for_numeros(
        ["V0123-24"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert r1.newly_persisted[0].persisted is not None
    assert r1.newly_persisted[0].persisted.was_new

    r2 = run_ingest_for_numeros(
        ["V0123-24"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert r2.newly_persisted == []
    assert r2.accepted[0].persisted is not None
    assert not r2.accepted[0].persisted.was_new


def test_pipeline_numero_no_encontrado_va_a_errored(tmp_path: Path) -> None:
    client = LocalDgtClient(root_dir=FIXTURES)
    report = run_ingest_for_numeros(
        ["V0123-24", "V9999-99", "V0456-24"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert len(report.accepted) == 2
    assert len(report.errored) == 1
    assert "V9999-99" in {o.numero for o in report.errored}


def test_pipeline_numero_invalido_va_a_errored(tmp_path: Path) -> None:
    client = LocalDgtClient(root_dir=FIXTURES)
    report = run_ingest_for_numeros(
        ["C0001-24"],  # no vinculante.
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert len(report.errored) == 1
    assert "NO vinculante" in (report.errored[0].error or "")


def test_pipeline_dry_run_no_escribe(tmp_path: Path) -> None:
    client = LocalDgtClient(root_dir=FIXTURES)
    report = run_ingest_for_numeros(
        ["V0123-24"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
        persist=False,
    )
    assert len(report.accepted) == 1
    assert report.newly_persisted == []
    assert not any(tmp_path.rglob("*.json"))


def test_impuesto_breakdown_cuenta_por_impuesto(tmp_path: Path) -> None:
    client = LocalDgtClient(root_dir=FIXTURES)
    report = run_ingest_for_numeros(
        ["V0123-24", "V0456-24", "V0789-24"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    counts = impuesto_breakdown(report)
    assert counts["irpf"] == 1
    assert counts["iva"] == 1
    assert counts["is"] == 1
    assert counts["isd"] == 0


def test_pipeline_promocion_manual_no_se_pisa(tmp_path: Path) -> None:
    """Si un revisor edita el JSON a mano (cambia criterio_confidence a
    manual sin tocar content_hash), la próxima pasada NO debe sobrescribir."""
    import json

    client = LocalDgtClient(root_dir=FIXTURES)
    run_ingest_for_numeros(
        ["V0123-24"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    path = tmp_path / "2024" / "V0123-24.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["criterio_confidence"] = "manual"
    data["criterio"] = "Criterio validado por revisor humano."
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    run_ingest_for_numeros(
        ["V0123-24"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    final = load_consulta(path)
    assert final.criterio_confidence == CriterioConfidence.MANUAL
    assert final.criterio == "Criterio validado por revisor humano."
