"""Tests del análisis de impacto del drift contra el corpus.

El módulo `rag.impact` es 100% puro: dado un conjunto de `DriftItem` +
`BrokenRegionalURL` + corpus + escalas, devuelve qué reglas del corpus
citan cada fuente afectada y renderiza markdown listo para issue.

Tests cubren:

1. Cruce básico: un DriftItem en art. 57 LIRPF debe listar las
   deducciones del corpus real que lo citan.
2. DriftItem en una fuente que NADIE del corpus cita: el reporte la
   incluye con lista vacía (NO se silencia: queda visible en el issue).
3. Múltiples deducciones citando la misma fuente: ordenadas y deduplicadas.
4. Markdown render: encabezados, hashes, listas, y caso "sin findings".
5. JSON round-trip: `ImpactReport.to_dict()` debe ser serializable y
   reproducir los mismos datos clave.
"""

from __future__ import annotations

import json
from pathlib import Path

from hacienda_ai.deductions import load_deductions
from hacienda_ai.irpf import load_tax_scales
from hacienda_ai.rag.impact import (
    BrokenRegionalURL,
    DriftItem,
    ImpactReport,
    analyze_impact,
    render_markdown,
    write_report_json,
)


def _drift(boe_id: str, article: str, ded_id: str = "x") -> DriftItem:
    return DriftItem(
        boe_id=boe_id,
        article=article,
        declared_hash="aaa",
        computed_hash="bbb",
        deduction_id=ded_id,
    )


def test_analyze_impact_lists_affected_deductions() -> None:
    corpus = load_deductions()
    scales = load_tax_scales()
    drift = [_drift("BOE-A-2006-20764", "art. 57", "es_minimo_contribuyente_general_2024")]
    report = analyze_impact(drift, [], corpus, scales)
    affected = report.affected_deductions["BOE-A-2006-20764|art. 57"]
    # El art. 57 LIRPF es el mínimo del contribuyente: cita en 2024 y 2025.
    assert "es_minimo_contribuyente_general_2024" in affected
    assert "es_minimo_contribuyente_general_2025" in affected


def test_analyze_impact_lists_affected_scales() -> None:
    corpus = load_deductions()
    scales = load_tax_scales()
    drift = [_drift("BOE-A-2006-20764", "art. 63", "x")]
    report = analyze_impact(drift, [], corpus, scales)
    affected = report.affected_scales["BOE-A-2006-20764|art. 63"]
    assert "es_irpf_estatal_general_2024" in affected
    assert "es_irpf_estatal_general_2025" in affected


def test_analyze_impact_handles_fuente_sin_citas_del_corpus() -> None:
    """Una fuente con drift que ningún elemento del corpus actual cita:
    el reporte la mantiene visible (no se silencia), con lista vacía."""
    corpus = load_deductions()
    scales = load_tax_scales()
    drift = [_drift("BOE-A-9999-12345", "art. 1")]
    report = analyze_impact(drift, [], corpus, scales)
    assert report.affected_deductions == {}
    assert report.affected_scales == {}
    assert report.drift_items[0].boe_id == "BOE-A-9999-12345"
    md = render_markdown(report)
    assert "BOE-A-9999-12345" in md
    assert "Ninguna deducción/escala del corpus actual" in md


def test_analyze_impact_normalizes_article_case_and_spaces() -> None:
    """`Art. 57` y `art. 57` deben cruzarse contra el mismo bucket."""
    corpus = load_deductions()
    scales = load_tax_scales()
    drift = [_drift("BOE-A-2006-20764", "  ART. 57  ")]
    report = analyze_impact(drift, [], corpus, scales)
    # La clave del item conserva el texto original, pero el cruce debe
    # encontrar las deducciones igualmente.
    assert any(
        "es_minimo_contribuyente_general_2024" in v
        for v in report.affected_deductions.values()
    )


def test_analyze_impact_sorts_and_deduplicates_affected_ids() -> None:
    corpus = load_deductions()
    scales = load_tax_scales()
    drift = [_drift("BOE-A-2006-20764", "art. 57")]
    report = analyze_impact(drift, [], corpus, scales)
    affected = report.affected_deductions["BOE-A-2006-20764|art. 57"]
    assert affected == sorted(set(affected))


def test_render_markdown_no_findings_returns_stable_line() -> None:
    report = ImpactReport(drift_items=(), broken_urls=())
    md = render_markdown(report)
    assert "Sin findings" in md


def test_render_markdown_includes_drift_details() -> None:
    corpus = load_deductions()
    scales = load_tax_scales()
    drift = [_drift("BOE-A-2006-20764", "art. 57", "es_minimo_contribuyente_general_2024")]
    report = analyze_impact(drift, [], corpus, scales)
    md = render_markdown(report)
    assert "## Drift detectado contra BOE consolidado" in md
    assert "BOE-A-2006-20764" in md
    assert "art. 57" in md
    assert "es_minimo_contribuyente_general_2024" in md


def test_render_markdown_includes_broken_urls_section() -> None:
    broken = [
        BrokenRegionalURL(
            url="https://www.bocm.es/foo.pdf",
            boe_id="BOCM-2024-12345",
            deduction_id="mad_alquiler_joven_2024",
            status_code=404,
            error=None,
        )
    ]
    report = ImpactReport(drift_items=(), broken_urls=tuple(broken))
    md = render_markdown(report)
    assert "Enlaces a boletines autonómicos rotos" in md
    assert "https://www.bocm.es/foo.pdf" in md
    assert "HTTP 404" in md
    assert "mad_alquiler_joven_2024" in md


def test_write_report_json_round_trip(tmp_path: Path) -> None:
    report = ImpactReport(
        drift_items=(_drift("BOE-A-2006-20764", "art. 57", "x"),),
        broken_urls=(
            BrokenRegionalURL(
                url="https://example/x",
                boe_id="BOCM-2024-1",
                deduction_id="x",
                status_code=500,
                error=None,
            ),
        ),
        affected_deductions={"BOE-A-2006-20764|art. 57": ["x"]},
        affected_scales={},
    )
    out = tmp_path / "report.json"
    write_report_json(report, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["drift_items"][0]["boe_id"] == "BOE-A-2006-20764"
    assert payload["broken_urls"][0]["status_code"] == 500
    assert payload["affected_deductions"]["BOE-A-2006-20764|art. 57"] == ["x"]


def test_has_findings_flag() -> None:
    empty = ImpactReport(drift_items=(), broken_urls=())
    assert not empty.has_findings
    with_drift = ImpactReport(
        drift_items=(_drift("BOE-A-X-1", "art. 1"),), broken_urls=()
    )
    assert with_drift.has_findings
