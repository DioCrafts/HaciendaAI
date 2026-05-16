"""Tests del parser INFORMA."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from hacienda_ai.models import ManualFuente
from hacienda_ai.rag.manuals import (
    InformaParseError,
    parse_informa_html,
)

FIXTURES = Path(__file__).parent / "fixtures" / "manuals"
INFORMA_HTML = (FIXTURES / "informa_sample.html").read_text(encoding="utf-8")


def test_parse_informa_devuelve_chunks_por_faq() -> None:
    chunks = parse_informa_html(INFORMA_HTML, today=date(2024, 9, 1))
    # El fixture tiene 2 FAQs.
    assert len(chunks) == 2
    assert all(c.fuente == ManualFuente.INFORMA_FAQ for c in chunks)


def test_chunks_informa_traen_numero_en_titulo_y_chunk_id() -> None:
    chunks = parse_informa_html(INFORMA_HTML, today=date(2024, 9, 1))
    titulos = [c.titulo for c in chunks]
    assert any("137456" in t for t in titulos)
    assert any("138999" in t for t in titulos)
    ids = [c.chunk_id for c in chunks]
    assert any("faq137456" in cid for cid in ids)


def test_chunks_informa_contienen_pregunta_y_respuesta() -> None:
    chunks = parse_informa_html(INFORMA_HTML, today=date(2024, 9, 1))
    for c in chunks:
        # El contenido lleva la estructura Pregunta/Respuesta verbatim.
        assert "Pregunta:" in c.contenido
        assert "Respuesta:" in c.contenido


def test_chunks_informa_detectan_materia_como_subseccion() -> None:
    chunks = parse_informa_html(INFORMA_HTML, today=date(2024, 9, 1))
    materias = {c.subseccion for c in chunks}
    assert "IRPF" in materias
    assert "IVA" in materias


def test_chunks_informa_extraen_normativa_de_cabecera() -> None:
    chunks = parse_informa_html(INFORMA_HTML, today=date(2024, 9, 1))
    irpf_faq = next(c for c in chunks if c.subseccion == "IRPF")
    assert irpf_faq.referencias_normativas  # no vacío.
    joined = " ".join(irpf_faq.referencias_normativas).lower()
    assert "35/2006" in joined


def test_parse_informa_acepta_texto_plano() -> None:
    """Sin tags HTML, el parser igualmente extrae FAQs si la estructura está."""
    plain = (
        "Nº 100001\n"
        "Materia: IRPF\n"
        "Pregunta: ¿Pregunta de prueba?\n"
        "Respuesta: Respuesta de prueba.\n"
    )
    chunks = parse_informa_html(plain, today=date(2024, 9, 1))
    assert len(chunks) == 1
    assert "100001" in chunks[0].titulo


def test_parse_informa_html_vacio_lanza() -> None:
    with pytest.raises(InformaParseError):
        parse_informa_html("", today=date(2024, 9, 1))


def test_parse_informa_sin_faqs_lanza() -> None:
    with pytest.raises(InformaParseError):
        parse_informa_html(
            "<html><body><p>Texto sin FAQs.</p></body></html>",
            today=date(2024, 9, 1),
        )


def test_chunk_content_hash_es_sha256() -> None:
    chunks = parse_informa_html(INFORMA_HTML, today=date(2024, 9, 1))
    for c in chunks:
        assert len(c.content_hash) == 64
