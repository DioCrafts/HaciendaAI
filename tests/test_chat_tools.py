"""Tests de las tools expuestas al LLM.

Cada tool se verifica de forma aislada: input mínimo, salida JSON-serializable,
y manejo limpio de errores (un input malo no debe levantar excepción — debe
devolver `{"error": "..."}` para que el LLM pueda reformular sin romper el
loop del orquestador).
"""

from __future__ import annotations

import json

import pytest

from hacienda_ai.chat.tools import build_default_registry, serialize_tool_result
from hacienda_ai.deductions import load_deductions
from hacienda_ai.irpf import load_tax_scales
from hacienda_ai.normas import load_norma_registry


@pytest.fixture(scope="module")
def registry():
    corpus = load_deductions()
    norma_registry = load_norma_registry()
    scales = load_tax_scales()
    return build_default_registry(
        deductions=corpus, registry=norma_registry, scales=scales
    )


def test_registry_exposes_expected_tools(registry) -> None:
    names = {spec["name"] for spec in registry.specs}
    assert names == {
        "get_deduction_catalog",
        "search_norma",
        "evaluate_profile",
        "compute_irpf_quota",
        "verify_citation",
    }


def test_tool_specs_have_anthropic_compatible_schema(registry) -> None:
    """Cada spec debe tener `name`, `description` y `input_schema` con
    `type: object`. Es lo que la API de Anthropic exige en `tools=[...]`."""
    for spec in registry.specs:
        assert spec["name"]
        assert spec["description"]
        assert spec["input_schema"]["type"] == "object"


def test_get_deduction_catalog_filters_by_year_and_scope(registry) -> None:
    r = registry.dispatch(
        "get_deduction_catalog", {"tax_year": 2024, "scope": "estatal"}
    )
    assert r["count"] > 0
    assert all(d["tax_year"] == 2024 and d["scope"] == "estatal" for d in r["deductions"])


def test_get_deduction_catalog_returns_pinpoint_sources(registry) -> None:
    r = registry.dispatch("get_deduction_catalog", {"tax_year": 2024})
    sample = next(d for d in r["deductions"] if d["id"].startswith("es_minimo"))
    assert sample["sources"][0]["boe_id"].startswith("BOE-A-")
    assert sample["sources"][0]["article"]


def test_search_norma_finds_by_keyword(registry) -> None:
    r = registry.dispatch("search_norma", {"query": "maternidad"})
    assert r["deduction_matches"], "no encontró deducciones de maternidad"
    assert any("maternidad" in d["name"].lower() for d in r["deduction_matches"])


def test_search_norma_requires_query(registry) -> None:
    r = registry.dispatch("search_norma", {})
    assert "error" in r


def test_evaluate_profile_with_real_profile(registry) -> None:
    r = registry.dispatch(
        "evaluate_profile",
        {
            "profile": {
                "tax_year": 2024,
                "region": "Madrid",
                "filing_mode": "individual",
                "personal": {"has_disability": False},
                "family": {"children_count": 1, "ascendants_count": 0},
                "income": {"work_gross": 30000, "work_net": 27500},
                "expenses": {},
                "documents": ["Libro de familia o certificado de convivencia"],
            }
        },
    )
    assert "evaluations" in r
    applies = [e for e in r["evaluations"] if e["status"] == "applies"]
    ids = {e["deduction_id"] for e in applies}
    assert "es_minimo_contribuyente_general_2024" in ids
    assert "es_minimo_descendientes_tramo_1_2024" in ids


def test_evaluate_profile_rejects_invalid_payload(registry) -> None:
    r = registry.dispatch("evaluate_profile", {"profile": "not-a-dict"})
    assert "error" in r


def test_evaluate_profile_rejects_missing_field(registry) -> None:
    r = registry.dispatch("evaluate_profile", {"profile": {"region": "Madrid"}})
    assert "error" in r


def test_compute_irpf_quota_returns_full_breakdown(registry) -> None:
    r = registry.dispatch(
        "compute_irpf_quota",
        {
            "profile": {
                "tax_year": 2024,
                "region": "Madrid",
                "filing_mode": "individual",
                "personal": {"has_disability": False},
                "family": {"children_count": 1, "ascendants_count": 0},
                "income": {"work_gross": 30000, "work_net": 27500},
                "expenses": {},
                "documents": ["Libro de familia o certificado de convivencia"],
            }
        },
    )
    # Mismos importes que el motor verifica (test_quota.py).
    assert r["cuota_integra_estatal"] == pytest.approx(2452.50, abs=0.01)
    assert r["minimo_personal_familiar"] == 7950.0
    assert r["cuota_integra_autonomica"] is None
    assert any("Madrid" in n for n in r["notes"])


def test_verify_citation_blocks_inventado(registry) -> None:
    r = registry.dispatch(
        "verify_citation",
        {"text": "El art. 999 LIRPF dice X.", "devengo_date": "2024-12-31"},
    )
    assert r["verdict"] == "block"
    assert any(b["code"] == "ARTICLE_NOT_IN_CORPUS" for b in r["blocking_issues"])


def test_verify_citation_passes_real(registry) -> None:
    r = registry.dispatch(
        "verify_citation",
        {
            "text": "El art. 57 LIRPF fija el mínimo en 5.550 €.",
            "devengo_date": "2024-12-31",
        },
    )
    assert r["verdict"] == "safe"


def test_dispatch_unknown_tool_returns_error(registry) -> None:
    r = registry.dispatch("tool_que_no_existe", {})
    assert "error" in r and "desconocida" in r["error"].lower()


def test_serialize_tool_result_is_json_round_trippable(registry) -> None:
    r = registry.dispatch("get_deduction_catalog", {"tax_year": 2024})
    serialized = serialize_tool_result(r)
    assert json.loads(serialized)["count"] == r["count"]
