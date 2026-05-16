"""Tests del verificador BOE (lógica pura, sin red)."""

from __future__ import annotations

import importlib.util
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "verify_seed", REPO_ROOT / "scripts" / "verify_seed.py"
)
assert _SPEC is not None and _SPEC.loader is not None
verify_seed = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verify_seed)


SAMPLE_BLOCK = """
<bloque id="a57" tipo="precepto" titulo="Artículo 57">
  <version id_norma="X" fecha_publicacion="20061129" fecha_vigencia="20070101" fecha_vigencia_fin="20141231">
    <p class="articulo">Artículo 57. Mínimo del contribuyente.</p>
    <p class="parrafo">1. Importe antiguo.</p>
    <p class="nota_pie">Se modifica por la Ley 26/2014.</p>
  </version>
  <version id_norma="Y" fecha_publicacion="20141128" fecha_vigencia="20150101">
    <p class="articulo">Artículo 57. Mínimo del contribuyente.</p>
    <p class="parrafo">1. El mínimo del contribuyente será, con carácter general, de 5.550 euros anuales.</p>
    <p class="nota_pie">Se modifica por el art. 1.33 de la Ley 26/2014, de 27 de noviembre.</p>
  </version>
</bloque>
"""


def _wrap(xml_block: str) -> str:
    return f'<?xml version="1.0"?>\n<response><data><texto>{xml_block}</texto></data></response>'


def test_parse_article_id_handles_common_shapes() -> None:
    assert verify_seed.parse_article_id("art. 57") == "a57"
    assert verify_seed.parse_article_id("Artículo 81") == "a81"
    assert verify_seed.parse_article_id("art. 81 bis") == "a81bis"
    assert verify_seed.parse_article_id("art 19") == "a19"
    assert verify_seed.parse_article_id("boe:dtdecimoquinta") == "dtdecimoquinta"
    assert verify_seed.parse_article_id("texto que no parsea") is None


def test_select_version_picks_active_at_target_date() -> None:
    body = verify_seed.find_block(_wrap(SAMPLE_BLOCK), "a57")
    selected = verify_seed.select_version(body, date(2025, 6, 1))
    text = verify_seed.normalize_version_text(selected)
    assert "5.550 euros" in text
    assert "Importe antiguo" not in text


def test_select_version_picks_historical_for_past_target() -> None:
    body = verify_seed.find_block(_wrap(SAMPLE_BLOCK), "a57")
    selected = verify_seed.select_version(body, date(2013, 6, 1))
    text = verify_seed.normalize_version_text(selected)
    assert "Importe antiguo" in text
    assert "5.550 euros" not in text


def test_normalize_excludes_nota_pie() -> None:
    """El hash debe ser estable frente a nuevas notas de modificación BOE."""
    body = verify_seed.find_block(_wrap(SAMPLE_BLOCK), "a57")
    selected = verify_seed.select_version(body, date(2025, 6, 1))
    text = verify_seed.normalize_version_text(selected)
    assert "Se modifica" not in text, "El texto normativo no debe incluir nota_pie"


def test_compute_hash_is_deterministic_and_changes_with_text() -> None:
    h1, _ = verify_seed.compute_hash(_wrap(SAMPLE_BLOCK), "a57", date(2025, 6, 1))
    h2, _ = verify_seed.compute_hash(_wrap(SAMPLE_BLOCK), "a57", date(2025, 6, 1))
    assert h1 == h2
    h_old, _ = verify_seed.compute_hash(_wrap(SAMPLE_BLOCK), "a57", date(2013, 6, 1))
    assert h_old != h1


def test_find_block_raises_for_missing_id() -> None:
    import pytest

    with pytest.raises(verify_seed.BoeFetchError, match="bloque"):
        verify_seed.find_block(_wrap(SAMPLE_BLOCK), "a999")
