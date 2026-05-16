"""Test de integración del pipeline de ingesta BOE.

Mockea solo la capa HTTP (`BoeClient`) y verifica que el pipeline:

1. Descarga el sumario, parsea, clasifica, descarga documentos relevantes,
   hashea y construye `Norma`/`VersionNorma`.
2. Persiste en `data/normas/boe_ingested_YYYY.json` particionando por año
   del `enacted_at`.
3. Es idempotente: una segunda ejecución sobre el mismo sumario no
   duplica entradas.
4. Trata 404 como "día sin publicación" sin error.
5. Carga el JSON resultante con `NormaRegistry.from_dict` sin solapamientos
   ni referencias rotas.

Los fixtures viven en `tests/fixtures/boe/`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from hacienda_ai.models import NormaRegistry, NormaStatus, SourceKind
from hacienda_ai.rag.ingestion import (
    BoeNotFoundError,
    IngestionReport,
    run_ingestion_for_date,
)

FIXTURES = Path(__file__).parent / "fixtures" / "boe"


class FakeBoeClient:
    """Stub del cliente BOE para tests.

    `summaries` mapea fecha → payload (string) ya en JSON o XML.
    `documents` mapea boe_id → XML del documento.
    Cualquier clave ausente se trata como 404.

    Lleva un contador de llamadas reales (no caché) para verificar
    idempotencia.
    """

    def __init__(
        self,
        summaries: dict[date, tuple[str, str]],
        documents: dict[str, str],
    ) -> None:
        self.summaries = summaries
        self.documents = documents
        self.summary_calls = 0
        self.document_calls = 0

    def fetch_summary(self, target: date) -> tuple[str, str]:
        self.summary_calls += 1
        if target not in self.summaries:
            raise BoeNotFoundError(f"404 simulado para {target.isoformat()}")
        return self.summaries[target]

    def fetch_document_xml(self, boe_id: str) -> str:
        self.document_calls += 1
        if boe_id not in self.documents:
            raise BoeNotFoundError(f"404 simulado para {boe_id}")
        return self.documents[boe_id]


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def fake_client() -> FakeBoeClient:
    sumario_json = _load("sumario_20240130.json")
    doc_xml = _load("documento_BOE-A-2024-1700.xml")
    # Para la Ley IRPF y el RD Industria no tenemos fixtures dedicadas;
    # reutilizamos el mismo XML (con metadatos genéricos) — el pipeline
    # solo necesita texto hasheable, no metadata coherente.
    return FakeBoeClient(
        summaries={date(2024, 1, 30): (sumario_json, "application/json")},
        documents={
            "BOE-A-2024-1699": doc_xml,
            "BOE-A-2024-1700": doc_xml,
            # NO incluimos BOE-A-2024-1701 (industria, no fiscal) ni
            # BOE-A-2024-1702 (nombramiento). Si el filtro fiscal funciona,
            # el pipeline no debe pedirlos.
        },
    )


def test_pipeline_ingiere_solo_normativa_fiscal(
    fake_client: FakeBoeClient, tmp_path: Path
) -> None:
    report = run_ingestion_for_date(
        date(2024, 1, 30),
        client=fake_client,  # type: ignore[arg-type]
        normas_dir=tmp_path,
    )

    # Sumario tiene 4 items, solo 2 son fiscalmente relevantes (Ley IRPF y
    # Orden HFP). RD Industria y nombramiento se descartan.
    assert report.total_summary_items == 4
    accepted_ids = {item.boe_id for item, _ in report.accepted}
    assert accepted_ids == {"BOE-A-2024-1699", "BOE-A-2024-1700"}

    # Solo se descargan documentos de los aceptados.
    assert fake_client.document_calls == 2
    assert not report.fetch_errors


def test_pipeline_persiste_normas_particionadas_por_anyo(
    fake_client: FakeBoeClient, tmp_path: Path
) -> None:
    report = run_ingestion_for_date(
        date(2024, 1, 30), client=fake_client, normas_dir=tmp_path  # type: ignore[arg-type]
    )

    # La Ley 5/2024 "de 28 de enero" y la Orden HFP/115/2024 "de 25 de enero"
    # tienen enacted_at en 2024 → se persisten en boe_ingested_2024.json.
    partition = tmp_path / "boe_ingested_2024.json"
    assert partition.exists()
    assert report.added_count == 2

    data = json.loads(partition.read_text(encoding="utf-8"))
    norma_ids = {n["boe_id"] for n in data["normas"]}
    assert norma_ids == {"BOE-A-2024-1699", "BOE-A-2024-1700"}

    # Cada norma tiene exactamente una versión inicial vigente.
    version_ids = {v["norma_boe_id"] for v in data["versions"]}
    assert version_ids == norma_ids
    for v in data["versions"]:
        assert v["status"] == NormaStatus.VIGENTE.value
        assert v["effective_from"] == "2024-01-31"
        assert "content_hash" in v


def test_pipeline_es_idempotente(
    fake_client: FakeBoeClient, tmp_path: Path
) -> None:
    # Primera ejecución: añade.
    r1 = run_ingestion_for_date(
        date(2024, 1, 30), client=fake_client, normas_dir=tmp_path  # type: ignore[arg-type]
    )
    assert r1.added_count == 2
    assert r1.duplicate_count == 0

    # Segunda ejecución: todo duplicado, nada añadido, sin conflictos.
    r2 = run_ingestion_for_date(
        date(2024, 1, 30), client=fake_client, normas_dir=tmp_path  # type: ignore[arg-type]
    )
    assert r2.added_count == 0
    assert r2.duplicate_count == 2
    assert r2.conflict_count == 0


def test_pipeline_detecta_conflicto_si_hash_difiere(
    fake_client: FakeBoeClient, tmp_path: Path
) -> None:
    # Primera ejecución con el documento original.
    run_ingestion_for_date(
        date(2024, 1, 30), client=fake_client, normas_dir=tmp_path  # type: ignore[arg-type]
    )

    # Mutamos el documento simulando una corrección del BOE.
    altered_doc = fake_client.documents["BOE-A-2024-1700"].replace(
        "Anguila,", "Anguila, Andorra,"
    )
    fake_client.documents["BOE-A-2024-1700"] = altered_doc

    r2 = run_ingestion_for_date(
        date(2024, 1, 30), client=fake_client, normas_dir=tmp_path  # type: ignore[arg-type]
    )
    # La Ley sigue siendo duplicada (su doc no cambió); la Orden ahora es
    # conflicto.
    assert r2.conflict_count == 1
    assert r2.duplicate_count == 1


def test_pipeline_trata_404_como_dia_sin_publicacion(tmp_path: Path) -> None:
    client = FakeBoeClient(summaries={}, documents={})
    report = run_ingestion_for_date(
        date(2024, 1, 28),  # domingo: sin BOE
        client=client,  # type: ignore[arg-type]
        normas_dir=tmp_path,
    )
    assert report.no_publication
    assert report.total_summary_items == 0
    assert report.added_count == 0


def test_pipeline_dry_run_no_escribe_a_disco(
    fake_client: FakeBoeClient, tmp_path: Path
) -> None:
    report = run_ingestion_for_date(
        date(2024, 1, 30),
        client=fake_client,  # type: ignore[arg-type]
        normas_dir=tmp_path,
        dry_run=True,
    )
    assert len(report.built) == 2
    assert report.added_count == 0
    assert not (tmp_path / "boe_ingested_2024.json").exists()


def test_corpus_resultante_es_cargable_por_norma_registry(
    fake_client: FakeBoeClient, tmp_path: Path
) -> None:
    """El JSON generado debe ser válido como input de `NormaRegistry.from_dict`.

    Sin esto, el corpus se rompería en tiempo de carga del proceso real.
    """
    run_ingestion_for_date(
        date(2024, 1, 30), client=fake_client, normas_dir=tmp_path  # type: ignore[arg-type]
    )
    data = json.loads(
        (tmp_path / "boe_ingested_2024.json").read_text(encoding="utf-8")
    )
    registry = NormaRegistry.from_dict(
        {"normas": data["normas"], "versions": data["versions"]}
    )
    assert registry.knows("BOE-A-2024-1700")
    # Devuelve la versión vigente en la fecha esperada.
    target = date(2024, 6, 1)
    version = registry.version_at("BOE-A-2024-1700", target)
    assert version is not None
    assert version.status == NormaStatus.VIGENTE
    # Y el kind quedó correctamente persistido y deserializado.
    norma = registry.get_norma("BOE-A-2024-1700")
    assert norma is not None
    assert norma.kind == SourceKind.ORDEN_MINISTERIAL


def test_report_audit_trail_es_completo(
    fake_client: FakeBoeClient, tmp_path: Path
) -> None:
    """El IngestionReport debe permitir reconstruir qué pasó para el body del PR."""
    report: IngestionReport = run_ingestion_for_date(
        date(2024, 1, 30), client=fake_client, normas_dir=tmp_path  # type: ignore[arg-type]
    )

    rejected_ids = {item.boe_id for item, _ in report.rejected}
    # RD Industria (no fiscal) y nombramiento (epigrafe no normativo).
    assert rejected_ids == {"BOE-A-2024-1701", "BOE-A-2024-1702"}

    # Para cada built tenemos identidad + clasificación + hash.
    for built in report.built:
        assert built.classification.kind is not None
        assert len(built.version.content_hash or "") == 64
        assert built.source_item.boe_id == built.norma.boe_id
