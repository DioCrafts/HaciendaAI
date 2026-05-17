"""Tests de integración del pipeline de manuales AEAT."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from hacienda_ai.models import ManualFuente
from hacienda_ai.rag.manuals import (
    StubPdfExtractor,
    ingest_informa_html,
    ingest_manual_pdf,
    load_chunk,
)

FIXTURES = Path(__file__).parent / "fixtures" / "manuals"
MANUAL_TXT = FIXTURES / "manual_irpf_2024_sample.txt"
INFORMA_HTML = FIXTURES / "informa_sample.html"


def test_ingest_manual_pdf_produce_chunks_con_metadata(tmp_path: Path) -> None:
    report = ingest_manual_pdf(
        MANUAL_TXT,
        fuente=ManualFuente.MANUAL_IRPF,
        ejercicio=2024,
        today=date(2024, 9, 1),
        root_dir=tmp_path,
        extractor=StubPdfExtractor(),
    )
    assert report.error is None
    assert len(report.chunks) >= 4  # ≥ una hoja por subsección + preámbulos.
    # Persistido en disco.
    assert len(report.newly_persisted) == len(report.chunks)


def test_ingest_manual_pdf_persiste_particionado_por_fuente_ejercicio(
    tmp_path: Path,
) -> None:
    report = ingest_manual_pdf(
        MANUAL_TXT,
        fuente=ManualFuente.MANUAL_IRPF,
        ejercicio=2024,
        today=date(2024, 9, 1),
        root_dir=tmp_path,
        extractor=StubPdfExtractor(),
    )
    # Estructura: <fuente>/<ejercicio>/<chunk_id_safe>.json
    base = tmp_path / "manual_irpf" / "2024"
    assert base.exists()
    json_files = list(base.glob("*.json"))
    assert len(json_files) == len(report.chunks)
    # Los nombres tienen `__` en vez de `::`.
    assert all("__" in f.name and "::" not in f.name for f in json_files)


def test_ingest_manual_pdf_enriquece_chunks_con_referencias_normativas(
    tmp_path: Path,
) -> None:
    """Los chunks que mencionan `Ley 35/2006 art. X` deben llevar la cita."""
    report = ingest_manual_pdf(
        MANUAL_TXT,
        fuente=ManualFuente.MANUAL_IRPF,
        ejercicio=2024,
        today=date(2024, 9, 1),
        root_dir=tmp_path,
        extractor=StubPdfExtractor(),
    )
    any_with_refs = any(
        c.referencias_normativas for c in report.chunks
    )
    assert any_with_refs, "esperábamos al menos un chunk con referencias detectadas"


def test_ingest_manual_pdf_idempotente_no_reescribe(tmp_path: Path) -> None:
    r1 = ingest_manual_pdf(
        MANUAL_TXT,
        fuente=ManualFuente.MANUAL_IRPF,
        ejercicio=2024,
        today=date(2024, 9, 1),
        root_dir=tmp_path,
        extractor=StubPdfExtractor(),
    )
    assert all(p.was_new for p in r1.persisted)

    r2 = ingest_manual_pdf(
        MANUAL_TXT,
        fuente=ManualFuente.MANUAL_IRPF,
        ejercicio=2024,
        today=date(2024, 9, 1),
        root_dir=tmp_path,
        extractor=StubPdfExtractor(),
    )
    assert all(not p.was_new for p in r2.persisted)
    assert r2.newly_persisted == []


def test_ingest_manual_pdf_dry_run_no_escribe(tmp_path: Path) -> None:
    report = ingest_manual_pdf(
        MANUAL_TXT,
        fuente=ManualFuente.MANUAL_IRPF,
        ejercicio=2024,
        today=date(2024, 9, 1),
        root_dir=tmp_path,
        extractor=StubPdfExtractor(),
        persist=False,
    )
    assert len(report.chunks) > 0
    assert report.persisted == []
    assert not any(tmp_path.rglob("*.json"))


def test_ingest_manual_pdf_chunks_son_cargables(tmp_path: Path) -> None:
    report = ingest_manual_pdf(
        MANUAL_TXT,
        fuente=ManualFuente.MANUAL_IRPF,
        ejercicio=2024,
        today=date(2024, 9, 1),
        root_dir=tmp_path,
        extractor=StubPdfExtractor(),
    )
    # Verifica round-trip a través de disco.
    for persisted in report.persisted:
        loaded = load_chunk(persisted.path)
        assert loaded.chunk_id == persisted.chunk.chunk_id
        assert loaded.content_hash == persisted.chunk.content_hash


def test_ingest_manual_pdf_error_si_pdf_inexistente(tmp_path: Path) -> None:
    report = ingest_manual_pdf(
        tmp_path / "no-existe.txt",
        fuente=ManualFuente.MANUAL_IRPF,
        ejercicio=2024,
        today=date(2024, 9, 1),
        root_dir=tmp_path,
        extractor=StubPdfExtractor(),
    )
    assert report.error is not None
    assert "no encontrado" in report.error.lower()


def test_ingest_informa_persiste_faqs(tmp_path: Path) -> None:
    report = ingest_informa_html(
        INFORMA_HTML,
        today=date(2024, 9, 1),
        root_dir=tmp_path,
    )
    assert report.error is None
    assert len(report.chunks) == 2
    base = tmp_path / "informa_faq" / "undated"
    assert base.exists()
    files = list(base.glob("*.json"))
    assert len(files) == 2


def test_ingest_informa_chunks_combinan_referencias_header_y_cuerpo(
    tmp_path: Path,
) -> None:
    report = ingest_informa_html(
        INFORMA_HTML,
        today=date(2024, 9, 1),
        root_dir=tmp_path,
    )
    # Cada FAQ del fixture lleva `Normativa: Ley 35/2006 art. X` en cabecera
    # Y menciones en el cuerpo. El runner combina ambas listas.
    for chunk in report.chunks:
        assert chunk.referencias_normativas  # no vacío.


def test_ingest_informa_idempotente(tmp_path: Path) -> None:
    r1 = ingest_informa_html(
        INFORMA_HTML, today=date(2024, 9, 1), root_dir=tmp_path
    )
    r2 = ingest_informa_html(
        INFORMA_HTML, today=date(2024, 9, 1), root_dir=tmp_path
    )
    assert all(p.was_new for p in r1.persisted)
    assert r2.newly_persisted == []


def test_ingest_informa_promocion_humana_no_se_pisa(tmp_path: Path) -> None:
    """Si un humano edita el contenido de un chunk a mano sin cambiar el
    content_hash, la próxima ingesta NO debe sobrescribirlo."""
    import json

    r1 = ingest_informa_html(
        INFORMA_HTML, today=date(2024, 9, 1), root_dir=tmp_path
    )
    path = r1.persisted[0].path
    data = json.loads(path.read_text(encoding="utf-8"))
    data["titulo"] = "Título editado por revisor humano"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    ingest_informa_html(
        INFORMA_HTML, today=date(2024, 9, 1), root_dir=tmp_path
    )
    final = load_chunk(path)
    assert final.titulo == "Título editado por revisor humano"


def test_ingest_manual_rechaza_fuente_informa_en_pipeline_pdf(
    tmp_path: Path,
) -> None:
    """No mezclar pipelines: `ingest_manual_pdf` con fuente INFORMA debe fallar."""
    report = ingest_manual_pdf(
        MANUAL_TXT,
        fuente=ManualFuente.INFORMA_FAQ,
        ejercicio=None,
        today=date(2024, 9, 1),
        root_dir=tmp_path,
        extractor=StubPdfExtractor(),
    )
    assert report.error is not None
    assert "INFORMA" in report.error
