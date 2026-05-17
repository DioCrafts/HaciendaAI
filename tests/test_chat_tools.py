"""Tests de las tools expuestas al LLM.

Cada tool se verifica de forma aislada: input mínimo, salida JSON-serializable,
y manejo limpio de errores (un input malo no debe levantar excepción — debe
devolver `{"error": "..."}` para que el LLM pueda reformular sin romper el
loop del orquestador).
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from hacienda_ai.chat.tools import build_default_registry, serialize_tool_result
from hacienda_ai.deductions import load_deductions
from hacienda_ai.irpf import load_tax_scales
from hacienda_ai.normas import load_norma_registry
from hacienda_ai.rag.vector import (
    EmbeddedChunk,
    SourceType,
    VectorMatch,
    VectorQuery,
)


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
        "get_fiscal_calendar",
        "lookup_iva_type",
        "compute_iva_quota",
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


# ---------- retrieve_legal_context ----------


class _RecordingRetriever:
    """Stub retriever determinista que registra las queries que recibe."""

    def __init__(self, matches: list[VectorMatch]) -> None:
        self._matches = list(matches)
        self.calls: list[VectorQuery] = []

    def search(self, query: VectorQuery) -> list[VectorMatch]:
        self.calls.append(query)
        return list(self._matches)


def _norma_match(
    chunk_id: str = "norma::BOE-A-2006-20764::art-19",
    boe_id: str = "BOE-A-2006-20764",
    articulo: str = "art. 19",
    apartado: str | None = "2.e)",
    impuesto: str = "irpf",
    score: float = 0.91,
) -> VectorMatch:
    metadata = {
        "boe_id": boe_id,
        "articulo": articulo,
        "impuesto": impuesto,
        "effective_from": "2015-01-01",
    }
    if apartado is not None:
        metadata["apartado"] = apartado
    chunk = EmbeddedChunk(
        chunk_id=chunk_id,
        source_type=SourceType.NORMA,
        text=(
            "Articulo 19. Rendimientos netos del trabajo. Gastos de defensa "
            "juridica deducibles con limite 300 €."
        ),
        embedding=(0.0,),
        embedding_model="stub",
        metadata=metadata,
    )
    return VectorMatch(chunk=chunk, score=score)


def _dgt_match(
    chunk_id: str = "consulta_dgt::V0123-24",
    numero: str = "V0123-24",
    fecha: str = "2024-01-30",
) -> VectorMatch:
    chunk = EmbeddedChunk(
        chunk_id=chunk_id,
        source_type=SourceType.CONSULTA_DGT,
        text="Criterio DGT sobre gastos de defensa jurídica…",
        embedding=(0.0,),
        embedding_model="stub",
        metadata={
            "numero": numero,
            "fecha": fecha,
            "impuesto": "irpf",
        },
    )
    return VectorMatch(chunk=chunk, score=0.83)


@pytest.fixture
def rag_registry():
    """Registry construida con un stub retriever (6 tools en lugar de 5)."""
    corpus = load_deductions()
    norma_registry = load_norma_registry()
    scales = load_tax_scales()
    retriever = _RecordingRetriever([_norma_match(), _dgt_match()])
    reg = build_default_registry(
        deductions=corpus,
        registry=norma_registry,
        scales=scales,
        retriever=retriever,
    )
    return reg, retriever


def test_retrieve_tool_is_only_registered_when_retriever_is_injected(
    registry, rag_registry
) -> None:
    """Sin retriever → 5 tools. Con retriever → 6 tools (la sexta es la nueva)."""
    base_names = {spec["name"] for spec in registry.specs}
    assert "retrieve_legal_context" not in base_names
    reg, _ = rag_registry
    extended_names = {spec["name"] for spec in reg.specs}
    assert "retrieve_legal_context" in extended_names
    assert extended_names == base_names | {"retrieve_legal_context"}


def test_retrieve_tool_returns_numbered_sources_with_citation_hints(
    rag_registry,
) -> None:
    reg, retriever = rag_registry
    r = reg.dispatch(
        "retrieve_legal_context",
        {
            "query": "gastos defensa jurídica IRPF",
            "impuesto": "irpf",
            "devengo_date": "2024-12-31",
            "top_k": 5,
        },
    )

    # El retriever recibió un VectorQuery con los filtros traducidos.
    assert len(retriever.calls) == 1
    query = retriever.calls[0]
    assert query.text == "gastos defensa jurídica IRPF"
    assert query.impuesto == "irpf"
    assert query.fecha_devengo == date(2024, 12, 31)
    assert query.top_k == 5
    assert query.source_types is None

    # Payload bien formado para el LLM.
    assert r["count"] == 2
    assert len(r["sources"]) == 2
    assert [s["index"] for s in r["sources"]] == [1, 2]
    assert r["filters"]["impuesto"] == "irpf"
    assert r["filters"]["devengo_date"] == "2024-12-31"
    assert r["filters"]["source_types"] is None

    # Cada fuente trae rendered con [FUENTE N] y un citation_hint útil.
    norma = next(s for s in r["sources"] if s["source_type"] == "norma")
    assert norma["rendered"].startswith("[FUENTE")
    assert "BOE-A-2006-20764" in norma["rendered"]
    assert norma["citation_hint"] == "art. 19.2.e) (BOE-A-2006-20764)"

    dgt = next(s for s in r["sources"] if s["source_type"] == "consulta_dgt")
    assert dgt["citation_hint"] == "Consulta DGT V0123-24 (2024-01-30)"

    # `rendered_context` es la concatenación pronta para inyectar.
    assert "[FUENTE 1]" in r["rendered_context"]
    assert "[FUENTE 2]" in r["rendered_context"]


def test_retrieve_tool_filters_source_types(rag_registry) -> None:
    reg, retriever = rag_registry
    reg.dispatch(
        "retrieve_legal_context",
        {
            "query": "deducción autonómica",
            "source_types": ["norma", "consulta_dgt"],
        },
    )
    assert retriever.calls[-1].source_types == (
        SourceType.NORMA,
        SourceType.CONSULTA_DGT,
    )


def test_retrieve_tool_requires_query(rag_registry) -> None:
    reg, _ = rag_registry
    assert "error" in reg.dispatch("retrieve_legal_context", {})
    assert "error" in reg.dispatch("retrieve_legal_context", {"query": "   "})


def test_retrieve_tool_rejects_invalid_devengo_date(rag_registry) -> None:
    reg, _ = rag_registry
    r = reg.dispatch(
        "retrieve_legal_context",
        {"query": "x", "devengo_date": "31-12-2024"},
    )
    assert "error" in r and "devengo_date" in r["error"].lower()


def test_retrieve_tool_rejects_unknown_source_type(rag_registry) -> None:
    reg, _ = rag_registry
    r = reg.dispatch(
        "retrieve_legal_context",
        {"query": "x", "source_types": ["norma", "circular_aeat"]},
    )
    assert "error" in r and "source_types" in r["error"].lower()


def test_retrieve_tool_rejects_top_k_out_of_range(rag_registry) -> None:
    reg, _ = rag_registry
    assert "error" in reg.dispatch(
        "retrieve_legal_context", {"query": "x", "top_k": 0}
    )
    assert "error" in reg.dispatch(
        "retrieve_legal_context", {"query": "x", "top_k": 100}
    )
    assert "error" in reg.dispatch(
        "retrieve_legal_context", {"query": "x", "top_k": "5"}
    )


def test_retrieve_tool_returns_empty_when_no_matches() -> None:
    corpus = load_deductions()
    norma_registry = load_norma_registry()
    scales = load_tax_scales()
    retriever = _RecordingRetriever([])
    reg = build_default_registry(
        deductions=corpus,
        registry=norma_registry,
        scales=scales,
        retriever=retriever,
    )
    r = reg.dispatch("retrieve_legal_context", {"query": "pregunta esotérica"})
    assert r == {
        "count": 0,
        "sources": [],
        "rendered_context": "",
        "filters": {
            "impuesto": None,
            "devengo_date": None,
            "source_types": None,
            "top_k": 6,
        },
    }


def test_retrieve_tool_handles_retriever_exception_gracefully() -> None:
    class _Boom:
        def search(self, query: VectorQuery) -> list[VectorMatch]:
            raise RuntimeError("Qdrant unreachable")

    corpus = load_deductions()
    norma_registry = load_norma_registry()
    scales = load_tax_scales()
    reg = build_default_registry(
        deductions=corpus,
        registry=norma_registry,
        scales=scales,
        retriever=_Boom(),
    )
    r = reg.dispatch("retrieve_legal_context", {"query": "x"})
    assert "error" in r and "Qdrant unreachable" in r["error"]


def test_retrieve_tool_payload_is_json_serializable(rag_registry) -> None:
    reg, _ = rag_registry
    r = reg.dispatch("retrieve_legal_context", {"query": "test"})
    # `serialize_tool_result` es lo que el orquestador usa para mandar el
    # tool_result de vuelta al LLM: debe sobrevivir el round-trip JSON.
    decoded = json.loads(serialize_tool_result(r))
    assert decoded["count"] == r["count"]
    assert decoded["sources"][0]["index"] == 1


# ---------- get_fiscal_calendar ----------


def test_fiscal_calendar_tool_returns_resolution_and_events(registry) -> None:
    r = registry.dispatch(
        "get_fiscal_calendar",
        {"today_override": "2026-05-17", "window_days": 60},
    )
    assert r["today"] == "2026-05-17"
    resolution = r["fiscal_year_resolution"]
    assert resolution["in_progress_year"] == 2026
    assert resolution["last_closed_year"] == 2025
    assert resolution["recommended_for_irpf_query"] == 2025
    assert resolution["renta_campaign"]["is_open"] is True
    # Renta 2025 vence el 30-jun-2026 (a 44 días).
    assert any(
        e["code"] == "100" and e["period_label"] == "Ejercicio 2025"
        for e in r["upcoming_events"]
    )


def test_fiscal_calendar_tool_filters_by_segment(registry) -> None:
    r = registry.dispatch(
        "get_fiscal_calendar",
        {
            "today_override": "2026-05-17",
            "window_days": 365,
            "segments": ["particular"],
        },
    )
    codes = {e["code"] for e in r["upcoming_events"]}
    assert "100" in codes  # particular declara IRPF
    assert "303" not in codes  # IVA no aplica a particular sin actividad
    assert r["filters"]["segments"] == ["particular"]


def test_fiscal_calendar_tool_rejects_invalid_today_override(registry) -> None:
    r = registry.dispatch(
        "get_fiscal_calendar",
        {"today_override": "17/05/2026"},
    )
    assert "error" in r and "today_override" in r["error"].lower()


def test_fiscal_calendar_tool_rejects_window_out_of_range(registry) -> None:
    assert "error" in registry.dispatch(
        "get_fiscal_calendar",
        {"window_days": 0},
    )
    assert "error" in registry.dispatch(
        "get_fiscal_calendar",
        {"window_days": 1000},
    )


def test_fiscal_calendar_tool_rejects_unknown_segment(registry) -> None:
    r = registry.dispatch(
        "get_fiscal_calendar",
        {"segments": ["particular", "extraterrestre"]},
    )
    assert "error" in r and "segments" in r["error"].lower()


def test_fiscal_calendar_tool_defaults_to_today(registry) -> None:
    """Sin `today_override` usa `date.today()` y nunca devuelve eventos
    pasados."""
    r = registry.dispatch("get_fiscal_calendar", {"window_days": 365})
    today_iso = r["today"]
    for event in r["upcoming_events"]:
        assert event["deadline"] >= today_iso


def test_fiscal_calendar_tool_payload_is_json_serializable(registry) -> None:
    r = registry.dispatch(
        "get_fiscal_calendar",
        {"today_override": "2026-05-17", "window_days": 30},
    )
    decoded = json.loads(serialize_tool_result(r))
    assert decoded["today"] == "2026-05-17"
    assert "upcoming_events" in decoded


# ---------- lookup_iva_type / compute_iva_quota ----------


def test_lookup_iva_type_returns_matches_with_pinpoint(registry) -> None:
    r = registry.dispatch("lookup_iva_type", {"query": "libros"})
    assert r["count"] >= 1
    sample = r["matches"][0]
    assert sample["tipo"] == "superreducido"
    assert sample["source"]["boe_id"] == "BOE-A-1992-28740"
    assert sample["source"]["article"].startswith("art. 91")


def test_lookup_iva_type_rejects_empty_query(registry) -> None:
    assert "error" in registry.dispatch("lookup_iva_type", {"query": ""})
    assert "error" in registry.dispatch("lookup_iva_type", {})


def test_lookup_iva_type_no_match_returns_zero(registry) -> None:
    r = registry.dispatch("lookup_iva_type", {"query": "xyzwxyz123"})
    assert r["count"] == 0
    assert r["matches"] == []


def test_compute_iva_quota_general(registry) -> None:
    r = registry.dispatch(
        "compute_iva_quota",
        {"base_imponible": 100, "tipo": "general"},
    )
    assert r["rate"] == 0.21
    assert r["cuota"] == 21.0
    assert r["total"] == 121.0
    assert r["source"]["article"] == "art. 90"


def test_compute_iva_quota_exento_returns_null_cuota(registry) -> None:
    r = registry.dispatch(
        "compute_iva_quota",
        {"base_imponible": 1000, "tipo": "exento"},
    )
    assert r["rate"] is None
    assert r["cuota"] is None
    assert "exenta" in r["note"].lower()


def test_compute_iva_quota_rejects_negative_base(registry) -> None:
    r = registry.dispatch(
        "compute_iva_quota",
        {"base_imponible": -50, "tipo": "general"},
    )
    assert "error" in r and "negativa" in r["error"].lower()


def test_compute_iva_quota_rejects_unknown_tipo(registry) -> None:
    r = registry.dispatch(
        "compute_iva_quota",
        {"base_imponible": 100, "tipo": "fantasia"},
    )
    assert "error" in r and "tipo" in r["error"].lower()


def test_compute_iva_quota_rejects_missing_fields(registry) -> None:
    assert "error" in registry.dispatch("compute_iva_quota", {})
    assert "error" in registry.dispatch(
        "compute_iva_quota", {"base_imponible": 100}
    )
