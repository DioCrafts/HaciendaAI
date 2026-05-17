"""Tests del módulo IVA: tipos, cálculo de cuota, catálogo, búsqueda.

Cubre:

1. Tipos impositivos: rates correctos, distinción exento/cero, cita
   pinpoint por tipo apuntando a LIVA.
2. `compute_iva_quota`: aritmética básica, casos especiales (exento,
   cero), validación de inputs (base negativa, tipo inválido).
3. Catálogo: estructura, pinpoints consistentes, sin duplicados.
4. `lookup_iva_operations`: matching léxico, normalización ASCII y
   minúsculas, query vacía → [].
5. `iva_documented_sources`: deduplicación + cobertura.
6. Integración con `verify_citations`: una cita IVA correcta pasa el
   guard SOLO cuando se inyectan las sources documentadas.
"""

from __future__ import annotations

import pytest

from hacienda_ai.iva import (
    CATALOG,
    IVA_RATES,
    IVA_SOURCES,
    IVAComputationError,
    IVATipo,
    compute_iva_quota,
    iva_documented_sources,
    lookup_iva_operations,
)
from hacienda_ai.iva.tipos import LIVA_BOE_ID
from hacienda_ai.normas import load_norma_registry
from hacienda_ai.safety import verify_citations

# ---------- IVATipo / rates ----------


def test_iva_rates_match_law() -> None:
    """Los tipos vigentes siguen LIVA arts. 90 y 91."""
    assert IVA_RATES[IVATipo.GENERAL] == 0.21
    assert IVA_RATES[IVATipo.REDUCIDO] == 0.10
    assert IVA_RATES[IVATipo.SUPERREDUCIDO] == 0.04
    assert IVA_RATES[IVATipo.CERO] == 0.0
    # EXENTO no tiene rate (None) — distinto de cero 0%.
    assert IVA_RATES[IVATipo.EXENTO] is None


def test_iva_sources_all_point_to_liva() -> None:
    for tipo, source in IVA_SOURCES.items():
        assert source.boe_id == LIVA_BOE_ID, f"{tipo!r} sin LIVA boe_id"
        assert source.article, f"{tipo!r} sin artículo"
        assert "LIVA" in source.title


# ---------- compute_iva_quota ----------


def test_compute_quota_general() -> None:
    quota = compute_iva_quota(100.0, IVATipo.GENERAL)
    assert quota.rate == 0.21
    assert quota.cuota == 21.0
    assert quota.total == 121.0
    assert quota.source.boe_id == LIVA_BOE_ID
    assert quota.source.article == "art. 90"
    assert quota.note == ""


def test_compute_quota_reducido() -> None:
    quota = compute_iva_quota(50.0, IVATipo.REDUCIDO)
    assert quota.cuota == 5.0
    assert quota.total == 55.0


def test_compute_quota_superreducido() -> None:
    quota = compute_iva_quota(25.0, IVATipo.SUPERREDUCIDO)
    assert quota.cuota == 1.0
    assert quota.total == 26.0


def test_compute_quota_cero_has_quota_zero_with_note() -> None:
    """CERO ≠ EXENTO: la cuota es 0 € pero hay derecho a deducir el
    soportado (típicamente exportaciones)."""
    quota = compute_iva_quota(1000.0, IVATipo.CERO)
    assert quota.rate == 0.0
    assert quota.cuota == 0.0
    assert quota.total == 1000.0
    assert quota.note  # explicación presente.
    assert "derecho a deducir" in quota.note.lower()


def test_compute_quota_exento_returns_null_cuota() -> None:
    quota = compute_iva_quota(1000.0, IVATipo.EXENTO)
    assert quota.rate is None
    assert quota.cuota is None
    assert quota.total == 1000.0
    assert "exenta" in quota.note.lower()


def test_compute_quota_rounds_to_cents() -> None:
    quota = compute_iva_quota(33.33, IVATipo.GENERAL)
    # 33.33 × 0.21 = 6.9993 → redondeado 7.00 € (2 decimales).
    assert quota.cuota == 7.00


def test_compute_quota_negative_base_raises() -> None:
    with pytest.raises(IVAComputationError, match="negativa"):
        compute_iva_quota(-10.0, IVATipo.GENERAL)


def test_compute_quota_non_numeric_raises() -> None:
    with pytest.raises(IVAComputationError, match="numérica"):
        compute_iva_quota("100", IVATipo.GENERAL)  # type: ignore[arg-type]


def test_compute_quota_dict_roundtrip() -> None:
    quota = compute_iva_quota(200.0, IVATipo.GENERAL)
    payload = quota.to_dict()
    assert payload["base_imponible"] == 200.0
    assert payload["tipo"] == "general"
    assert payload["rate"] == 0.21
    assert payload["cuota"] == 42.0
    assert payload["source"]["article"] == "art. 90"


# ---------- Catálogo ----------


def test_catalog_is_non_empty_and_keywords_unique() -> None:
    assert len(CATALOG) >= 20
    keywords = [op.keyword for op in CATALOG]
    assert len(keywords) == len(set(keywords))


def test_catalog_all_sources_target_liva() -> None:
    for op in CATALOG:
        assert op.source.boe_id == LIVA_BOE_ID, op.keyword
        assert op.source.article


def test_catalog_covers_all_iva_tipos() -> None:
    tipos_present = {op.tipo for op in CATALOG}
    assert tipos_present == set(IVATipo)


def test_catalog_serializes_to_dict() -> None:
    op = CATALOG[0]
    payload = op.to_dict()
    assert payload["keyword"] == op.keyword
    assert payload["tipo"] == op.tipo.value
    assert payload["source"]["boe_id"] == LIVA_BOE_ID


# ---------- lookup_iva_operations ----------


def test_lookup_finds_books() -> None:
    matches = lookup_iva_operations("libros")
    assert any(m.tipo == IVATipo.SUPERREDUCIDO for m in matches)


def test_lookup_handles_accents_and_uppercase() -> None:
    """Normalización ASCII: la búsqueda 'HOSTELERÍA' debe encontrar
    'hosteleria'."""
    matches = lookup_iva_operations("HOSTELERÍA")
    assert any("hosteleria" in m.keyword for m in matches)


def test_lookup_requires_all_tokens() -> None:
    """Matching AND, no OR: 'alquiler vivienda' encuentra alquiler de
    vivienda; 'alquiler local' NO encuentra esa entrada (porque la
    descripción del alquiler de vivienda no contiene 'local')."""
    a = lookup_iva_operations("alquiler vivienda")
    assert any(m.tipo == IVATipo.EXENTO for m in a)
    b = lookup_iva_operations("alquiler local")
    # No debe encontrar la entrada de vivienda exenta — sería confuso.
    assert not any("vivienda" in m.keyword for m in b)


def test_lookup_empty_query_returns_empty() -> None:
    assert lookup_iva_operations("") == []
    assert lookup_iva_operations("   ") == []


def test_lookup_no_match_returns_empty() -> None:
    """Una operación esotérica que no está en el catálogo no debe
    devolver matches falsos."""
    assert lookup_iva_operations("xyzwxyz") == []


def test_lookup_servicios_profesionales_top_match_is_general() -> None:
    """`servicios profesionales` puede coincidir léxicamente con varias
    entradas (la descripción de servicios médicos contiene "profesionales");
    el matching es léxico AND, no semántico. Lo que sí garantizamos es
    que la entrada CANÓNICA aparezca y que sea de tipo general; el LLM
    se encarga de elegir la más relevante."""
    matches = lookup_iva_operations("servicios profesionales")
    assert matches
    # La entrada con keyword exacto "servicios profesionales" debe estar
    # entre los matches y ser de tipo general (LIVA art. 90).
    canonical = next(
        (m for m in matches if m.keyword == "servicios profesionales"), None
    )
    assert canonical is not None
    assert canonical.tipo == IVATipo.GENERAL


def test_lookup_medicamentos_is_superreducido() -> None:
    matches = lookup_iva_operations("medicamentos")
    assert any(m.tipo == IVATipo.SUPERREDUCIDO for m in matches)


# ---------- iva_documented_sources ----------


def test_documented_sources_dedupe_by_pinpoint() -> None:
    sources = iva_documented_sources()
    keys = [(s.boe_id, s.article, s.paragraph) for s in sources]
    assert len(keys) == len(set(keys))


def test_documented_sources_includes_main_articles() -> None:
    sources = iva_documented_sources()
    articles = {(s.boe_id, s.article) for s in sources}
    # Cabeceras esperadas.
    assert (LIVA_BOE_ID, "art. 90") in articles
    assert (LIVA_BOE_ID, "art. 91") in articles
    assert (LIVA_BOE_ID, "art. 20") in articles
    assert (LIVA_BOE_ID, "art. 21") in articles


# ---------- Integración con citation_guard ----------


def test_verify_citations_with_extra_sources_marks_iva_citation_safe() -> None:
    """Sin `extra_documented_sources`, una cita correcta a "art. 90 LIVA"
    se marcaría como ARTICLE_NOT_IN_CORPUS (el corpus IRPF no la
    incluye). Con las sources IVA inyectadas, debe pasar como `safe`."""
    registry = load_norma_registry()
    text = "El tipo general es del 21% (art. 90 LIVA, BOE-A-1992-28740)."

    # Sin sources IVA: la norma se reconoce (alias LIVA), pero el
    # artículo no figura en el corpus principal, así que el guard lo
    # marca como WARN (NORMA_HAS_NO_INDEXED_ARTICLES) o BLOCK
    # (ARTICLE_NOT_IN_CORPUS) según la lógica.
    bare = verify_citations(text, registry=registry)
    assert bare.verdict in ("warn",), bare.verdict

    # Con sources IVA inyectadas: el artículo está documentado y la
    # cita pasa como `safe`.
    with_iva = verify_citations(
        text,
        registry=registry,
        extra_documented_sources=list(iva_documented_sources()),
    )
    assert with_iva.verdict == "safe", with_iva.issues
