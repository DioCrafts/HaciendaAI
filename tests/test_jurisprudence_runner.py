"""Test de integración del pipeline CENDOJ.

Usa `LocalCendojClient` apuntando a los fixtures: el pipeline procesa
los 3 ECLIs, acepta los dos tributarios (TS-IRPF, AN-IVA), rechaza el
social, y persiste a `data/jurisprudencia/<organo>/<año>/<ECLI>.json`.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from hacienda_ai.models import (
    FalloSentido,
    Organo,
    RatioConfidence,
    Sentencia,
)
from hacienda_ai.rag.jurisprudence import (
    LocalCendojClient,
    load_sentencia,
    run_ingest_for_eclis,
)
from hacienda_ai.rag.jurisprudence.runner import organo_breakdown

FIXTURES = Path(__file__).parent / "fixtures" / "cendoj"


def test_pipeline_acepta_dos_tributarias_y_rechaza_social(tmp_path: Path) -> None:
    client = LocalCendojClient(root_dir=FIXTURES)
    report = run_ingest_for_eclis(
        [
            "ECLI:ES:TS:2024:1234",
            "ECLI:ES:AN:2024:567",
            "ECLI:ES:TS:2024:9999",
        ],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert len(report.accepted) == 2
    assert len(report.rejected) == 1
    assert len(report.errored) == 0
    accepted_eclis = {o.ecli for o in report.accepted}
    assert accepted_eclis == {"ECLI:ES:TS:2024:1234", "ECLI:ES:AN:2024:567"}
    rejected_eclis = {o.ecli for o in report.rejected}
    assert rejected_eclis == {"ECLI:ES:TS:2024:9999"}


def test_pipeline_persiste_sentencias_particionadas(tmp_path: Path) -> None:
    client = LocalCendojClient(root_dir=FIXTURES)
    report = run_ingest_for_eclis(
        ["ECLI:ES:TS:2024:1234", "ECLI:ES:AN:2024:567"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert len(report.newly_persisted) == 2

    # Estructura en disco: <organo>/<año>/<ECLI>.json
    ts_path = tmp_path / "ts" / "2024" / "ECLI:ES:TS:2024:1234.json"
    an_path = tmp_path / "an" / "2024" / "ECLI:ES:AN:2024:567.json"
    assert ts_path.exists()
    assert an_path.exists()

    # Cargable como `Sentencia` válida.
    ts = load_sentencia(ts_path)
    assert isinstance(ts, Sentencia)
    assert ts.organo == Organo.TS
    assert ts.fallo_sentido == FalloSentido.DESESTIMATORIA
    assert ts.ratio_confidence == RatioConfidence.AUTO
    assert ts.ratio_decidendi is not None


def test_pipeline_extrae_fallo_an_iva_estimatoria_parcial(tmp_path: Path) -> None:
    client = LocalCendojClient(root_dir=FIXTURES)
    report = run_ingest_for_eclis(
        ["ECLI:ES:AN:2024:567"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    s = report.accepted[0].sentencia
    assert s is not None
    assert s.fallo_sentido == FalloSentido.ESTIMATORIA_PARCIAL
    assert s.organo == Organo.AN


def test_pipeline_idempotente_no_reescribe(tmp_path: Path) -> None:
    client = LocalCendojClient(root_dir=FIXTURES)
    r1 = run_ingest_for_eclis(
        ["ECLI:ES:TS:2024:1234"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert r1.newly_persisted and r1.newly_persisted[0].persisted is not None
    assert r1.newly_persisted[0].persisted.was_new

    # Segunda pasada: detecta que ya existe con el mismo hash.
    r2 = run_ingest_for_eclis(
        ["ECLI:ES:TS:2024:1234"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert r2.newly_persisted == []
    assert r2.accepted[0].persisted is not None
    assert not r2.accepted[0].persisted.was_new


def test_pipeline_ecli_no_encontrado_no_aborta_resto(tmp_path: Path) -> None:
    client = LocalCendojClient(root_dir=FIXTURES)
    report = run_ingest_for_eclis(
        [
            "ECLI:ES:TS:2024:1234",
            "ECLI:ES:TS:9999:0000",  # no existe en fixtures.
            "ECLI:ES:AN:2024:567",
        ],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert len(report.accepted) == 2
    assert len(report.errored) == 1
    assert "ECLI:ES:TS:9999:0000" in {o.ecli for o in report.errored}


def test_pipeline_ecli_invalido_va_a_errored(tmp_path: Path) -> None:
    client = LocalCendojClient(root_dir=FIXTURES)
    report = run_ingest_for_eclis(
        ["esto-no-es-ecli"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    assert len(report.errored) == 1
    assert report.errored[0].error is not None
    assert "ECLI inválido" in report.errored[0].error


def test_pipeline_dry_run_no_escribe(tmp_path: Path) -> None:
    client = LocalCendojClient(root_dir=FIXTURES)
    report = run_ingest_for_eclis(
        ["ECLI:ES:TS:2024:1234"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
        persist=False,
    )
    assert len(report.accepted) == 1
    assert report.newly_persisted == []
    assert not any(tmp_path.rglob("*.json"))


def test_organo_breakdown_cuenta_por_organo(tmp_path: Path) -> None:
    client = LocalCendojClient(root_dir=FIXTURES)
    report = run_ingest_for_eclis(
        ["ECLI:ES:TS:2024:1234", "ECLI:ES:AN:2024:567"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    counts = organo_breakdown(report)
    assert counts["ts"] == 1
    assert counts["an"] == 1
    assert counts["tsj"] == 0


def test_pipeline_promocion_manual_no_se_pisa_en_segunda_pasada(
    tmp_path: Path,
) -> None:
    """Si un revisor edita el JSON a mano (promueve ratio_confidence a MANUAL
    y reemplaza el extracto sin tocar content_hash), la siguiente ejecución
    del cron NO debe sobrescribirlo.

    Esto es importante porque si el cron pisara la promoción humana,
    perderíamos la doctrina validada por un jurista.
    """
    import json

    client = LocalCendojClient(root_dir=FIXTURES)
    run_ingest_for_eclis(
        ["ECLI:ES:TS:2024:1234"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    path = tmp_path / "ts" / "2024" / "ECLI:ES:TS:2024:1234.json"

    # Simulamos la edición humana: el revisor abre el JSON, cambia el
    # extracto y promueve la confianza a manual. No toca `content_hash`
    # porque la fuente CENDOJ no ha cambiado.
    data = json.loads(path.read_text(encoding="utf-8"))
    data["ratio_confidence"] = "manual"
    data["ratio_decidendi"] = "Doctrina editada por revisor humano."
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    # Segunda pasada del cron: el content_hash de la fuente no ha cambiado,
    # así que persist_sentencia detecta hash coincidente y NO sobrescribe.
    run_ingest_for_eclis(
        ["ECLI:ES:TS:2024:1234"],
        client=client,
        root_dir=tmp_path,
        today=date(2024, 9, 1),
    )
    final = load_sentencia(path)
    assert final.ratio_confidence == RatioConfidence.MANUAL
    assert final.ratio_decidendi == "Doctrina editada por revisor humano."
