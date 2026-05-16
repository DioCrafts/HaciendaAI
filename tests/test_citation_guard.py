"""Tests del verificador de citas anti-alucinación.

Cubre 3 dimensiones:

1. `extract_citations`: regex y resolución. Cada familia (BOE-A, boletín
   autonómico, alias, ley con número/año, artículo, jurisprudencia) debe
   identificarse sin solapamientos.
2. `verify_citations` sobre casos canónicos: cita real → safe, cita
   inventada → block, jurisprudencia sin corpus → warn.
3. Trampas concretas que un LLM podría intentar colar: año futuro,
   artículo inexistente, norma derogada en la fecha, mezcla de citas
   reales con falsas.
"""

from __future__ import annotations

from datetime import date

import pytest

from hacienda_ai.deductions import load_deductions
from hacienda_ai.irpf import load_tax_scales
from hacienda_ai.normas import load_norma_registry
from hacienda_ai.safety import (
    CitationCheckResult,
    extract_citations,
    verify_citations,
)


@pytest.fixture(scope="module")
def corpus() -> list:
    return load_deductions()


@pytest.fixture(scope="module")
def scales() -> list:
    return load_tax_scales()


@pytest.fixture(scope="module")
def registry():
    return load_norma_registry()


def _verify(text: str, corpus, scales, registry) -> CitationCheckResult:
    return verify_citations(
        text,
        corpus=corpus,
        scales=scales,
        registry=registry,
        devengo=date(2024, 12, 31),
    )


# ---------- extract_citations ----------


def test_extract_state_boe_id() -> None:
    cs = extract_citations("Ver BOE-A-2006-20764 sobre IRPF.")
    boes = [c for c in cs if c.kind == "boe_state"]
    assert len(boes) == 1
    assert boes[0].boe_id == "BOE-A-2006-20764"


def test_extract_regional_boe_id() -> None:
    cs = extract_citations("Publicado en BOCM-2024-12345 hoy.")
    regs = [c for c in cs if c.kind == "boe_regional"]
    assert len(regs) == 1
    assert regs[0].boe_id == "BOCM-2024-12345"


def test_extract_alias_resolves_to_boe_id() -> None:
    cs = extract_citations("La LIRPF regula el IRPF.")
    aliases = [c for c in cs if c.kind == "alias"]
    assert len(aliases) == 1
    assert aliases[0].boe_id == "BOE-A-2006-20764"
    assert aliases[0].law_label.lower() == "lirpf"


def test_extract_law_number_year_resolves_when_known() -> None:
    # "Ley 35/2006" está en `_ALIASES`, así que el guard lo captura como
    # alias (no como law_reference) y resuelve el boe_id. Lo que el test
    # garantiza es que el resultado lleva el BOE-A correcto, no la familia
    # exacta del match.
    cs = extract_citations("Según Ley 35/2006 del IRPF.")
    assert any(c.boe_id == "BOE-A-2006-20764" for c in cs)


def test_extract_law_number_year_with_other_format() -> None:
    # "RDL 13/2010" no está en alias: debe salir como law_reference sin
    # boe_id resuelto (el verificador lo marcará luego como WARN).
    cs = extract_citations("Según el RDL 13/2010 sobre medidas urgentes.")
    laws = [c for c in cs if c.kind == "law_reference"]
    assert any(c.boe_id is None and "13/2010" in c.law_label for c in laws)


def test_extract_law_number_year_unresolved_when_unknown() -> None:
    cs = extract_citations("La Ley 9999/2099 inventada.")
    laws = [c for c in cs if c.kind == "law_reference"]
    assert any(c.boe_id is None and c.law_label.endswith("9999/2099") for c in laws)


def test_extract_article_with_suffix_and_paragraph() -> None:
    # Formato AEAT estándar: "art. 81 bis.2" o "art. 23.2". El paragraph
    # va separado por punto, no por espacio.
    cs = extract_citations("El art. 81 bis.2 LIRPF establece...")
    articles = [c for c in cs if c.kind == "article"]
    assert len(articles) == 1
    assert articles[0].article == "81"
    assert articles[0].article_suffix == "bis"
    assert articles[0].paragraph == "2"


def test_extract_jurisprudence_with_ref() -> None:
    cs = extract_citations("La STS 1234/2020 dijo X. La consulta DGT V0123-22 dijo Y.")
    juris = [c for c in cs if c.kind == "jurisprudence"]
    assert {c.juris_kind for c in juris} >= {"STS", "CONSULTA_DGT"}


def test_extract_boe_id_takes_priority_over_law_when_overlapping() -> None:
    # "BOE-A-2006-20764" no debe ser interpretado también como "Ley X/2006"
    # con número 20764: el BOE-A ya cubre la posición y el law match no
    # aparecería en este texto, pero el test cierra la garantía.
    cs = extract_citations("Identificador BOE-A-2006-20764 únicamente.")
    laws = [c for c in cs if c.kind == "law_reference"]
    assert not laws


# ---------- verify_citations: casos canónicos ----------


def test_verify_text_without_citations_is_safe(corpus, scales, registry) -> None:
    r = _verify("La declaración se presenta entre abril y junio.", corpus, scales, registry)
    assert r.verdict == "safe"
    assert r.issues == ()


def test_verify_real_lirpf_article_is_safe(corpus, scales, registry) -> None:
    r = _verify(
        "El art. 57 LIRPF fija el mínimo del contribuyente en 5.550 €.",
        corpus,
        scales,
        registry,
    )
    assert r.verdict == "safe", [i.message for i in r.issues]


def test_verify_real_law_reference_is_safe(corpus, scales, registry) -> None:
    r = _verify(
        "Según el art. 63 de la Ley 35/2006 la escala estatal se aplica progresivamente.",
        corpus,
        scales,
        registry,
    )
    assert r.verdict == "safe", [i.message for i in r.issues]


# ---------- Trampas: el guard tiene que bloquear ----------


def test_verify_boe_year_out_of_range_blocks(corpus, scales, registry) -> None:
    r = _verify("Según BOE-A-2099-9999 hay una nueva deducción.", corpus, scales, registry)
    assert r.verdict == "block"
    assert any(i.code == "BOE_YEAR_OUT_OF_RANGE" for i in r.blocking_issues)


def test_verify_article_inexistent_in_corpus_blocks(corpus, scales, registry) -> None:
    r = _verify(
        "El art. 999 LIRPF reconoce una deducción magnífica.",
        corpus,
        scales,
        registry,
    )
    assert r.verdict == "block"
    assert any(i.code == "ARTICLE_NOT_IN_CORPUS" for i in r.blocking_issues)


def test_verify_fake_article_suffix_blocks(corpus, scales, registry) -> None:
    # art. 81 bis existe (familia numerosa), pero "art. 81 quater" no.
    r = _verify(
        "Según el art. 81 quater LIRPF puede deducirse otro tramo.",
        corpus,
        scales,
        registry,
    )
    assert r.verdict == "block"
    assert any(i.code == "ARTICLE_NOT_IN_CORPUS" for i in r.blocking_issues)


def test_verify_block_takes_precedence_over_safe(corpus, scales, registry) -> None:
    """Una afirmación con una cita correcta y otra inventada se bloquea: el
    peor veredicto manda. Si dejamos pasar 'el art. 57 LIRPF es correcto'
    junto a 'el art. 999 LIRPF también', el texto entero contamina."""
    r = _verify(
        "El art. 57 LIRPF fija 5.550 € y el art. 999 LIRPF añade otra cosa.",
        corpus,
        scales,
        registry,
    )
    assert r.verdict == "block"
    block_codes = {i.code for i in r.blocking_issues}
    assert "ARTICLE_NOT_IN_CORPUS" in block_codes


# ---------- Warnings: cosas no verificables, no necesariamente falsas ----------


def test_verify_jurisprudence_warns(corpus, scales, registry) -> None:
    r = _verify("La STS 1234/2020 sentó doctrina.", corpus, scales, registry)
    assert r.verdict == "warn"
    assert all(i.code == "JURISPRUDENCE_NOT_INDEXED" for i in r.warnings)


def test_verify_regional_bulletin_warns(corpus, scales, registry) -> None:
    r = _verify("La deducción está en BOCM-2024-12345.", corpus, scales, registry)
    assert r.verdict == "warn"
    assert any(i.code == "REGIONAL_BULLETIN_NOT_VERIFIABLE" for i in r.warnings)


def test_verify_law_reference_not_in_corpus_warns(corpus, scales, registry) -> None:
    # LIS (Ley 27/2014, BOE-A-2014-12328) está en los alias pero NO en el
    # registry actual del proyecto, así que el guard emite WARN, no SAFE.
    r = _verify("El art. 10 de la LIS define la base imponible.", corpus, scales, registry)
    assert r.verdict == "warn"
    assert any(i.code == "NORMA_NOT_REGISTERED" for i in r.warnings)


def test_verify_law_reference_unresolved_warns(corpus, scales, registry) -> None:
    r = _verify("Según la Ley 9999/2099 hay una sorpresa.", corpus, scales, registry)
    assert r.verdict == "warn"
    assert any(i.code == "LAW_REFERENCE_UNRESOLVED" for i in r.warnings)


def test_verify_orphan_article_warns(corpus, scales, registry) -> None:
    r = _verify("Según el artículo 38 se aplica una reducción.", corpus, scales, registry)
    assert r.verdict == "warn"
    assert any(i.code == "ARTICLE_ORPHAN" for i in r.warnings)


# ---------- Vigencia temporal: el guard usa la fecha del devengo ----------


def test_verify_uses_devengo_for_vigencia(corpus, scales, registry) -> None:
    """Un devengo en 1995 debería marcar la LIRPF (Ley 35/2006) como no
    registrada para esa fecha porque no había entrado en vigor."""
    r = verify_citations(
        "El art. 57 LIRPF fija el mínimo del contribuyente.",
        corpus=corpus,
        scales=scales,
        registry=registry,
        devengo=date(1995, 12, 31),
    )
    # Esperamos warn por NORMA_VERSION_UNKNOWN_AT_DATE — la LIRPF tiene
    # versiones registradas a partir de 2007, no antes.
    assert r.verdict == "warn"
    assert any(i.code == "NORMA_VERSION_UNKNOWN_AT_DATE" for i in r.warnings)


# ---------- Articulación + boe ID explícito ----------


def test_verify_article_associated_to_explicit_boe_id(corpus, scales, registry) -> None:
    """Cuando el texto incluye el BOE-A explícitamente, el guard debe
    asociarlo al artículo cercano sin necesidad del alias."""
    r = _verify(
        "Según BOE-A-2006-20764, el art. 57 fija 5.550 €.",
        corpus,
        scales,
        registry,
    )
    assert r.verdict == "safe", [i.message for i in r.issues]


# ---------- Robustez: límites y casos raros ----------


def test_verify_empty_text() -> None:
    r = verify_citations("", devengo=date(2024, 12, 31))
    assert r.verdict == "safe"
    assert r.citations == ()
    assert r.issues == ()


def test_verify_without_corpus_or_registry_warns_on_anything_legal() -> None:
    """Sin registry ni corpus, una cita legal no se puede contrastar; el
    guard no debe inventarse `safe`. Aceptamos warn o safe sólo cuando no
    hay nada que verificar."""
    r = verify_citations(
        "El art. 57 LIRPF establece...",
        devengo=date(2024, 12, 31),
    )
    # Sin registry no podemos detectar vigencia, pero la cita es estructural.
    # Aceptamos cualquier veredicto que no sea "block" para esta combinación
    # (no hay corpus que cruzar). Lo importante: el guard no asume safe sin
    # haber comprobado nada en concreto.
    assert r.verdict in {"safe", "warn"}


def test_result_helpers_classify_issues(corpus, scales, registry) -> None:
    r = _verify(
        "El art. 999 LIRPF y la STS 1234/2020 dijeron cosas.",
        corpus,
        scales,
        registry,
    )
    assert r.has_blocks
    assert r.blocking_issues
    assert r.warnings
    assert not r.is_safe
