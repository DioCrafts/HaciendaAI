"""Tests del detector de estructura jerárquica de manuales AEAT."""

from __future__ import annotations

from pathlib import Path

from hacienda_ai.rag.manuals import (
    StructuralElementKind,
    StubPdfExtractor,
    detect_structure,
)
from hacienda_ai.rag.manuals.structure import iter_leaves

FIXTURES = Path(__file__).parent / "fixtures" / "manuals"


def _load_pages():
    return StubPdfExtractor().extract(FIXTURES / "manual_irpf_2024_sample.txt")


def test_extract_pages_separa_por_form_feed() -> None:
    pages = _load_pages()
    assert len(pages) == 4
    assert all(p.text for p in pages)
    # Páginas numeradas 1-based.
    assert [p.page_number for p in pages] == [1, 2, 3, 4]


def test_detect_structure_devuelve_root_con_capitulos() -> None:
    pages = _load_pages()
    root = detect_structure(pages)
    assert root.kind == StructuralElementKind.ROOT
    capitulos = root.children
    # El fixture tiene 2 capítulos.
    assert len(capitulos) == 2
    titulos = [c.title for c in capitulos]
    assert any("RENDIMIENTOS DEL TRABAJO" in t for t in titulos)
    assert any("CAPITAL INMOBILIARIO" in t for t in titulos)
    assert all(c.kind == StructuralElementKind.CAPITULO for c in capitulos)


def test_detect_structure_anida_secciones_y_subsecciones() -> None:
    pages = _load_pages()
    root = detect_structure(pages)
    cap1 = root.children[0]
    # Capítulo 1 tiene secciones 1.1 y 1.2.
    secciones = [c for c in cap1.children if c.kind == StructuralElementKind.SECCION]
    numbers = {s.numbering for s in secciones}
    assert "1.1" in numbers
    assert "1.2" in numbers
    # Cada sección contiene subsecciones.
    sec_1_1 = next(s for s in secciones if s.numbering == "1.1")
    sub_numbers = {sub.numbering for sub in sec_1_1.children}
    assert {"1.1.1", "1.1.2"}.issubset(sub_numbers)


def test_detect_structure_asigna_pagina_inicial_correcta() -> None:
    pages = _load_pages()
    root = detect_structure(pages)
    cap1 = root.children[0]
    # Capítulo 1 empieza en página 1.
    assert cap1.page_start == 1
    # Capítulo 2 (CAPITAL INMOBILIARIO) empieza en página 4.
    cap2 = root.children[1]
    assert cap2.page_start == 4


def test_detect_structure_devuelve_root_unico_si_sin_estructura() -> None:
    """Texto sin encabezados numerados → un solo nodo con todo el cuerpo."""
    from hacienda_ai.rag.manuals.pdf_extractor import PageText

    pages = [PageText(page_number=1, text="Texto suelto sin estructura.")]
    root = detect_structure(pages)
    assert root.kind == StructuralElementKind.ROOT
    assert root.children == []
    assert "sin estructura" in root.title.lower()
    assert "Texto suelto" in root.body


def test_iter_leaves_devuelve_subsecciones_y_preambulos() -> None:
    """Las hojas incluyen subsecciones y preámbulos de niveles intermedios."""
    pages = _load_pages()
    root = detect_structure(pages)
    leaves = iter_leaves(root)
    # `iter_leaves` devuelve pares (leaf, ancestors).
    numbers = [leaf.numbering for leaf, _ in leaves if leaf.numbering]
    # Las 4 subsecciones del fixture deben aparecer.
    assert "1.1.1" in numbers
    assert "1.1.2" in numbers
    assert "1.2.1" in numbers
    assert "2.1.1" in numbers


def test_iter_leaves_propaga_jerarquia_a_preambulos() -> None:
    """Los preámbulos sintéticos heredan los ancestros del nodo origen."""
    pages = _load_pages()
    root = detect_structure(pages)
    leaves = iter_leaves(root)
    # Localizamos el preámbulo de la sección 1.1.
    candidates = [
        (leaf, anc)
        for leaf, anc in leaves
        if "preámbulo" in leaf.title.lower() and leaf.numbering == "1.1"
    ]
    assert candidates, "esperaba el preámbulo sintético de la sección 1.1"
    _, ancestors = candidates[0]
    # El preámbulo sintético debe heredar el capítulo padre.
    cap = ancestors[StructuralElementKind.CAPITULO]
    assert cap is not None and cap.numbering == "1"
