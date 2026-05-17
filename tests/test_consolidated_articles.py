"""Tests del parser de bloques precepto del consolidado BOE.

Verifica las decisiones críticas del módulo:

1. Solo se iteran `<bloque tipo="precepto">` (el `prefacio` con
   `tipo="estructura"` se descarta).
2. `<p class="nota_pie*">` se excluye del hash — sin esto, cada
   anotación editorial de BOE generaría drift falso.
3. La selección de versión por fecha respeta intervalos
   `fecha_vigencia`/`fecha_vigencia_fin`.
4. Bloques sin versión vigente en la fecha se omiten del snapshot, no
   se hashean como cadena vacía.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from hacienda_ai.rag.consolidated import (
    all_block_hashes,
    iter_article_versions,
    iter_precept_blocks,
    normalize_version_text,
    select_version_for_date,
)
from hacienda_ai.rag.consolidated.articles import hash_block_at

FIXTURES = Path(__file__).parent / "fixtures" / "boe"
LIRPF_XML = (FIXTURES / "consolidado_lirpf_mini.xml").read_text(encoding="utf-8")


def test_iter_precept_blocks_filtra_estructura() -> None:
    ids = [bid for bid, _ in iter_precept_blocks(LIRPF_XML)]
    # El fixture tiene 5 preceptos y 1 bloque "estructura" (prefacio).
    assert ids == ["a1", "a2", "a19", "a81bis", "dadecimoctava"]
    assert "prefacio" not in ids


def test_select_version_para_2015_picks_version_reformada() -> None:
    """En 2015, el art. 19 debe devolver la versión post-reforma Ley 26/2014."""
    body = next(b for bid, b in iter_precept_blocks(LIRPF_XML) if bid == "a19")
    v = select_version_for_date(body, date(2015, 6, 1))
    assert v is not None
    text = normalize_version_text(v)
    # La versión 2015+ menciona "2.000 euros" (introducidos por la reforma).
    assert "2.000 euros" in text


def test_select_version_para_2010_picks_version_original() -> None:
    """En 2010, el art. 19 debe devolver la redacción original (sin "2.000 euros")."""
    body = next(b for bid, b in iter_precept_blocks(LIRPF_XML) if bid == "a19")
    v = select_version_for_date(body, date(2010, 6, 1))
    assert v is not None
    text = normalize_version_text(v)
    assert "2.000 euros" not in text


def test_select_version_devuelve_none_si_no_hay_version_vigente() -> None:
    """art. 81bis solo tiene versión desde 2015; en 2010 no hay vigente."""
    body = next(b for bid, b in iter_precept_blocks(LIRPF_XML) if bid == "a81bis")
    assert select_version_for_date(body, date(2010, 1, 1)) is None
    assert select_version_for_date(body, date(2015, 6, 1)) is not None


def test_normalize_version_text_excluye_notas_pie() -> None:
    """Los <p class="nota_pie*"> son metadato editorial y NO deben entrar al hash."""
    body = next(b for bid, b in iter_precept_blocks(LIRPF_XML) if bid == "a1")
    v = select_version_for_date(body, date(2010, 6, 1))
    assert v is not None
    text = normalize_version_text(v)
    # El texto normativo del art. 1 contiene "principios de igualdad".
    assert "igualdad" in text
    # La nota_pie del fixture dice "Original: Ley 35/2006, BOE núm. 285"; no debe colarse.
    assert "BOE" not in text and "285" not in text


def test_normalize_version_text_colapsa_whitespace() -> None:
    body = '<p class="parrafo">uno   dos\n\n\n  tres</p>'
    assert normalize_version_text(body) == "uno dos tres"


def test_hash_block_at_es_determinista() -> None:
    h1 = hash_block_at(LIRPF_XML, "a1", date(2010, 6, 1))
    h2 = hash_block_at(LIRPF_XML, "a1", date(2010, 6, 1))
    assert h1 is not None and h2 is not None
    assert h1.digest == h2.digest
    assert len(h1.digest) == 64
    assert h1.has_active_version


def test_hash_block_at_bloque_inexistente() -> None:
    assert hash_block_at(LIRPF_XML, "a999", date(2024, 1, 1)) is None


def test_all_block_hashes_omite_bloques_sin_version_vigente() -> None:
    """En 2010, el bloque `a81bis` (introducido en 2015) NO debe aparecer."""
    hashes = all_block_hashes(LIRPF_XML, date(2010, 6, 1))
    assert "a1" in hashes
    assert "a19" in hashes
    assert "a81bis" not in hashes


def test_all_block_hashes_para_2024_incluye_todos_los_vigentes() -> None:
    hashes = all_block_hashes(LIRPF_XML, date(2024, 1, 1))
    assert set(hashes.keys()) == {"a1", "a2", "a19", "a81bis", "dadecimoctava"}


def test_hash_cambia_si_cambia_la_redaccion_vigente() -> None:
    """Un cambio real en el texto normativo del bloque debe cambiar el hash."""
    original = all_block_hashes(LIRPF_XML, date(2024, 1, 1))
    # Mutamos el texto del art. 1: cambio sustantivo.
    mutated = LIRPF_XML.replace(
        "principios de igualdad",
        "principios de igualdad, equidad",
    )
    new = all_block_hashes(mutated, date(2024, 1, 1))
    assert original["a1"] != new["a1"]
    # El resto no debe cambiar.
    for bid in ("a2", "a19", "a81bis", "dadecimoctava"):
        assert original[bid] == new[bid]


def test_hash_NO_cambia_si_solo_cambia_una_nota_pie() -> None:
    """Editar una nota_pie no debe disparar drift — es metadato editorial."""
    original = all_block_hashes(LIRPF_XML, date(2010, 6, 1))
    mutated = LIRPF_XML.replace(
        "Original: Ley 35/2006, BOE núm. 285 de 29/11/2006.",
        "Modificado por nota editorial el 15/05/2026.",
    )
    new = all_block_hashes(mutated, date(2010, 6, 1))
    assert original == new


# ---------- iter_article_versions: timeline completo por artículo ----------


def test_iter_article_versions_devuelve_todas_las_versiones() -> None:
    """A diferencia de `all_block_hashes`, este iterador devuelve TODAS
    las versiones de cada bloque (no solo la vigente en una fecha)."""
    versions = list(
        iter_article_versions(LIRPF_XML, norma_boe_id="BOE-A-2006-20764")
    )
    # 5 preceptos × versiones: a1=2, a2=1, a19=2, a81bis=1, dadecimoctava=1 → 7
    assert len(versions) == 7


def test_iter_article_versions_inyecta_norma_boe_id() -> None:
    versions = list(
        iter_article_versions(LIRPF_XML, norma_boe_id="BOE-A-2006-20764")
    )
    assert all(v.norma_boe_id == "BOE-A-2006-20764" for v in versions)


def test_iter_article_versions_a19_tiene_dos_redacciones() -> None:
    versions = [
        v
        for v in iter_article_versions(LIRPF_XML, norma_boe_id="BOE-A-2006-20764")
        if v.article_id == "a19"
    ]
    assert len(versions) == 2
    versions.sort(key=lambda v: v.effective_from)
    assert versions[0].effective_from == date(2007, 1, 1)
    assert versions[0].effective_to == date(2014, 12, 31)
    assert versions[1].effective_from == date(2015, 1, 1)
    assert versions[1].effective_to is None
    # Los textos son diferentes y los hashes también.
    assert versions[0].text_hash != versions[1].text_hash
    # La reforma 2015 introduce el importe de 2.000 €.
    assert "2.000 euros" in versions[1].text


def test_iter_article_versions_excluye_notas_pie_del_texto() -> None:
    versions = [
        v
        for v in iter_article_versions(LIRPF_XML, norma_boe_id="BOE-A-2006-20764")
        if v.article_id == "a19"
    ]
    for v in versions:
        # Las notas editoriales del fixture NO deben aparecer en el texto.
        assert "Redaccion original" not in v.text
        assert "Reforma Ley 26/2014" not in v.text


def test_iter_article_versions_resuelve_modified_by_law_label() -> None:
    """Cuando la `<nota_pie>` cita 'Ley X/AAAA' sin BOE-A, devolvemos
    el label como fallback."""
    versions = [
        v
        for v in iter_article_versions(LIRPF_XML, norma_boe_id="BOE-A-2006-20764")
        if v.article_id == "a19" and v.effective_from == date(2015, 1, 1)
    ]
    assert len(versions) == 1
    assert versions[0].modified_by_boe_id == "law:Ley 26/2014"


def test_iter_article_versions_prefiere_boe_a_sobre_law_label() -> None:
    """Si la nota tiene BOE-A directo, ese gana sobre el label de ley."""
    xml = """<?xml version="1.0"?>
<legislacion-consolidada><texto>
<bloque id="a23" tipo="precepto">
  <version fecha_vigencia="20150101">
    <p class="parrafo">Texto.</p>
    <p class="nota_pie">Modificado por la Ley 26/2014, publicada en BOE-A-2014-12328.</p>
  </version>
</bloque>
</texto></legislacion-consolidada>"""
    versions = list(
        iter_article_versions(xml, norma_boe_id="BOE-A-2006-20764")
    )
    assert versions[0].modified_by_boe_id == "BOE-A-2014-12328"


def test_iter_article_versions_modified_by_none_sin_referencia() -> None:
    """Cuando no hay nota_pie con referencia parseable, devolvemos None."""
    xml = """<?xml version="1.0"?>
<legislacion-consolidada><texto>
<bloque id="a23" tipo="precepto">
  <version fecha_vigencia="20150101">
    <p class="parrafo">Texto sin referencia editorial.</p>
  </version>
</bloque>
</texto></legislacion-consolidada>"""
    versions = list(
        iter_article_versions(xml, norma_boe_id="BOE-A-2006-20764")
    )
    assert versions[0].modified_by_boe_id is None


def test_iter_article_versions_omite_versiones_sin_texto() -> None:
    """Una `<version>` con todos sus `<p>` siendo `nota_pie` (sin texto
    normativo) NO debe generar VersionArticulo — rompería la invariante
    del modelo (text no vacío)."""
    xml = """<?xml version="1.0"?>
<legislacion-consolidada><texto>
<bloque id="a23" tipo="precepto">
  <version fecha_vigencia="20150101">
    <p class="nota_pie">Solo nota editorial.</p>
  </version>
</bloque>
</texto></legislacion-consolidada>"""
    versions = list(
        iter_article_versions(xml, norma_boe_id="BOE-A-2006-20764")
    )
    assert versions == []


def test_iter_article_versions_text_hash_coincide_con_sha256_del_texto() -> None:
    import hashlib

    versions = list(
        iter_article_versions(LIRPF_XML, norma_boe_id="BOE-A-2006-20764")
    )
    for v in versions:
        expected = hashlib.sha256(v.text.encode("utf-8")).hexdigest()
        assert v.text_hash == expected
