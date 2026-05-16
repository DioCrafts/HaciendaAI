"""Test de integración del cron de detección de cambios legislativos.

Mockea el `ConsolidatedFetcher` para servir XMLs canónicos sin tocar
red. Verifica que el pipeline end-to-end:

1. Salta normas no estatales (boletines autonómicos).
2. Salta normas con versión vigente en estado derogada/inconstitucional.
3. Primera ejecución (bootstrap): crea snapshot, NO invalida cache RAG.
4. Segunda ejecución sin cambios: no toca nada.
5. Segunda ejecución con cambios: invalida cache RAG, borra cache de
   fetcher, reescribe snapshot.
6. Snapshot corrupto: error reportado, no se sobrescribe.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from hacienda_ai.models import (
    Norma,
    NormaRegistry,
    NormaStatus,
    SourceKind,
    VersionNorma,
)
from hacienda_ai.rag.cache import JsonAuditLog
from hacienda_ai.rag.consolidated import (
    ConsolidatedFetchError,
    load_snapshot,
    run_check_for_registry,
)

FIXTURES = Path(__file__).parent / "fixtures" / "boe"
LIRPF_XML = (FIXTURES / "consolidado_lirpf_mini.xml").read_text(encoding="utf-8")


class FakeFetcher:
    """Stub del `ConsolidatedFetcher`. Sirve XML por boe_id desde memoria.

    `invalidations` registra las llamadas a `invalidate()` para auditar
    que el runner las dispara cuando detecta drift.
    """

    def __init__(self, payloads: dict[str, str]) -> None:
        self.payloads = payloads
        self.invalidations: list[str] = []
        self.fetch_calls: list[str] = []

    def fetch(self, boe_id: str) -> str:
        self.fetch_calls.append(boe_id)
        if boe_id not in self.payloads:
            raise ConsolidatedFetchError(f"404 simulado para {boe_id}")
        return self.payloads[boe_id]

    def invalidate(self, boe_id: str) -> None:
        self.invalidations.append(boe_id)


@pytest.fixture
def registry() -> NormaRegistry:
    r = NormaRegistry()
    r.register_norma(
        Norma(
            boe_id="BOE-A-2006-20764",
            kind=SourceKind.LEY,
            title="LIRPF",
            enacted_at=date(2006, 11, 28),
        )
    )
    r.register_version(
        VersionNorma(
            norma_boe_id="BOE-A-2006-20764",
            effective_from=date(2007, 1, 1),
            status=NormaStatus.VIGENTE,
        )
    )
    # Norma derogada: el runner debe saltarla.
    r.register_norma(
        Norma(
            boe_id="BOE-A-1999-12345",
            kind=SourceKind.LEY,
            title="Ley X derogada",
            enacted_at=date(1999, 1, 1),
        )
    )
    r.register_version(
        VersionNorma(
            norma_boe_id="BOE-A-1999-12345",
            effective_from=date(1999, 1, 1),
            status=NormaStatus.DEROGADA,
        )
    )
    # Norma autonómica (boletín regional): el runner debe saltarla.
    r.register_norma(
        Norma(
            boe_id="BOCM-2024-1",
            kind=SourceKind.LEY,
            title="Ley autonómica Madrid",
            enacted_at=date(2024, 1, 1),
        )
    )
    r.register_version(
        VersionNorma(
            norma_boe_id="BOCM-2024-1",
            effective_from=date(2024, 1, 1),
            status=NormaStatus.VIGENTE,
        )
    )
    return r


def _run(
    registry: NormaRegistry,
    fetcher: FakeFetcher,
    tmp_path: Path,
    *,
    today: date = date(2024, 1, 1),
):
    log = JsonAuditLog(path=tmp_path / "rag_invalidations.json")
    return run_check_for_registry(
        registry,
        fetcher=fetcher,  # type: ignore[arg-type]
        snapshots_dir=tmp_path / "snapshots",
        rag_cache=log,
        reference_date=today,
        today=today,
    ), log


def test_runner_salta_no_estatales_y_derogadas(
    registry: NormaRegistry, tmp_path: Path
) -> None:
    fetcher = FakeFetcher({"BOE-A-2006-20764": LIRPF_XML})
    report, _ = _run(registry, fetcher, tmp_path)

    # Solo la LIRPF debe haberse comprobado.
    assert fetcher.fetch_calls == ["BOE-A-2006-20764"]
    skipped_ids = {o.boe_id for o in report.skipped}
    assert "BOE-A-1999-12345" in skipped_ids
    assert "BOCM-2024-1" in skipped_ids


def test_runner_bootstrap_crea_snapshot_sin_notificar(
    registry: NormaRegistry, tmp_path: Path
) -> None:
    fetcher = FakeFetcher({"BOE-A-2006-20764": LIRPF_XML})
    report, log = _run(registry, fetcher, tmp_path)

    # Bootstrap detectado, no es drift.
    assert len(report.bootstrap_outcomes) == 1
    assert report.drift_outcomes == []

    # Snapshot escrito.
    snap = load_snapshot(tmp_path / "snapshots", "BOE-A-2006-20764")
    assert snap is not None
    assert "a1" in snap.consolidated_articles

    # NO se invalidó cache RAG en bootstrap.
    assert log.recent_invalidations() == []
    # NO se invalidó cache del fetcher.
    assert fetcher.invalidations == []


def test_runner_segunda_ejecucion_sin_cambios_no_toca_nada(
    registry: NormaRegistry, tmp_path: Path
) -> None:
    fetcher = FakeFetcher({"BOE-A-2006-20764": LIRPF_XML})
    _run(registry, fetcher, tmp_path)
    report, log = _run(registry, fetcher, tmp_path)

    assert report.bootstrap_outcomes == []
    assert report.drift_outcomes == []
    assert log.recent_invalidations() == []
    assert fetcher.invalidations == []


def test_runner_detecta_drift_e_invalida_cache(
    registry: NormaRegistry, tmp_path: Path
) -> None:
    # Primera pasada con XML original → bootstrap.
    fetcher = FakeFetcher({"BOE-A-2006-20764": LIRPF_XML})
    _run(registry, fetcher, tmp_path)

    # Segunda pasada con XML mutado (art. 19 modificado).
    mutated = LIRPF_XML.replace(
        "2.000 euros anuales",
        "3.000 euros anuales",
    )
    fetcher = FakeFetcher({"BOE-A-2006-20764": mutated})
    report, log = _run(registry, fetcher, tmp_path)

    # Drift detectado en a19.
    assert len(report.drift_outcomes) == 1
    drift = report.drift_outcomes[0].drift
    assert drift is not None and drift.has_changes
    assert [m.block_id for m in drift.modified] == ["a19"]

    # Invalidación RAG registrada con los artículos afectados.
    invalidations = log.recent_invalidations()
    assert len(invalidations) == 1
    assert invalidations[0].boe_id == "BOE-A-2006-20764"
    assert "a19" in invalidations[0].articles

    # Cache del fetcher invalidada para forzar redescarga la próxima vez.
    assert fetcher.invalidations == ["BOE-A-2006-20764"]

    # Snapshot actualizado con el nuevo hash.
    snap = load_snapshot(tmp_path / "snapshots", "BOE-A-2006-20764")
    assert snap is not None
    assert snap.consolidated_articles["a19"] != ""  # tiene hash nuevo.


def test_runner_drift_articulo_eliminado(
    registry: NormaRegistry, tmp_path: Path
) -> None:
    fetcher = FakeFetcher({"BOE-A-2006-20764": LIRPF_XML})
    _run(registry, fetcher, tmp_path)

    # Eliminamos el bloque a81bis del XML (artículo derogado).
    import re

    no_81bis = re.sub(
        r'<bloque id="a81bis" tipo="precepto">.*?</bloque>',
        "",
        LIRPF_XML,
        flags=re.DOTALL,
    )
    fetcher = FakeFetcher({"BOE-A-2006-20764": no_81bis})
    report, log = _run(registry, fetcher, tmp_path)

    assert len(report.drift_outcomes) == 1
    drift = report.drift_outcomes[0].drift
    assert drift is not None
    assert [r.block_id for r in drift.removed] == ["a81bis"]
    assert "a81bis" in log.recent_invalidations()[0].articles


def test_runner_snapshot_corrupto_no_sobrescribe_y_reporta_error(
    registry: NormaRegistry, tmp_path: Path
) -> None:
    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir()
    corrupt_path = snapshots_dir / "BOE-A-2006-20764.json"
    corrupt_path.write_text("no es json")
    corrupt_before = corrupt_path.read_text()

    fetcher = FakeFetcher({"BOE-A-2006-20764": LIRPF_XML})
    report, _ = _run(registry, fetcher, tmp_path)

    assert len(report.errored) == 1
    assert "snapshot corrupto" in (report.errored[0].error or "")
    # No se sobrescribió el snapshot corrupto: operador puede investigar.
    assert corrupt_path.read_text() == corrupt_before


def test_runner_dry_run_no_persiste_nada(
    registry: NormaRegistry, tmp_path: Path
) -> None:
    fetcher = FakeFetcher({"BOE-A-2006-20764": LIRPF_XML})
    log = JsonAuditLog(path=tmp_path / "rag_invalidations.json")
    report = run_check_for_registry(
        registry,
        fetcher=fetcher,  # type: ignore[arg-type]
        snapshots_dir=tmp_path / "snapshots",
        rag_cache=log,
        reference_date=date(2024, 1, 1),
        today=date(2024, 1, 1),
        persist=False,
    )
    # Snapshot calculado pero NO escrito.
    assert not (tmp_path / "snapshots").exists()
    assert log.recent_invalidations() == []
    # Y aun así el reporte contiene el bootstrap detectado.
    assert len(report.bootstrap_outcomes) == 1


def test_runner_fetcher_error_se_reporta_pero_no_aborta(
    registry: NormaRegistry, tmp_path: Path
) -> None:
    # Fetcher sin payload para LIRPF → 404 simulado.
    fetcher = FakeFetcher({})
    report, _ = _run(registry, fetcher, tmp_path)
    assert len(report.errored) == 1
    assert report.errored[0].boe_id == "BOE-A-2006-20764"
    # El resto de normas siguen procesándose (saltadas en este caso).
    assert len(report.skipped) >= 1
