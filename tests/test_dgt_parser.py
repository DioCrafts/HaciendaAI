"""Tests del parser HTML de Petete."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from hacienda_ai.rag.dgt import parse_consulta_html
from hacienda_ai.rag.dgt.parser import (
    html_to_plain_text,
    parse_consulta_date,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dgt"
V_IRPF = (FIXTURES / "V0123-24.html").read_text(encoding="utf-8")
V_IVA = (FIXTURES / "V0456-24.html").read_text(encoding="utf-8")
V_IS = (FIXTURES / "V0789-24.html").read_text(encoding="utf-8")


def test_html_to_plain_text_preserva_pares_de_tabla() -> None:
    """`</td><td>` se reemplaza por ": " para conservar pares clave-valor."""
    raw = "<table><tr><td>Núm. Consulta</td><td>V0123-24</td></tr></table>"
    plain = html_to_plain_text(raw)
    # Tras normalizar, encontramos "Núm. Consulta: V0123-24" en una línea.
    assert any(
        "Núm. Consulta" in line and "V0123-24" in line for line in plain.split("\n")
    )


def test_parse_header_irpf() -> None:
    parsed = parse_consulta_html(V_IRPF)
    h = parsed.header_fields
    assert "V0123-24" in (parsed.get_field("Núm. Consulta") or "")
    assert "30/01/2024" in (parsed.get_field("Fecha Salida") or "")
    assert "Ley 35/2006" in (parsed.get_field("Normativa") or "")
    # Materia es campo opcional pero existe en el fixture.
    assert "IRPF" in (parsed.get_field("Materia") or "")
    # Nada del cuerpo debe haberse colado como cabecera.
    assert not any("hechos" in k.lower() for k in h)


def test_parse_header_iva() -> None:
    parsed = parse_consulta_html(V_IVA)
    assert "V0456-24" in (parsed.get_field("Núm. Consulta") or "")
    assert "Ley 37/1992" in (parsed.get_field("Normativa") or "")


def test_split_sections_localiza_las_tres_secciones() -> None:
    parsed = parse_consulta_html(V_IRPF)
    assert "DESCRIPCION_HECHOS" in parsed.secciones
    assert "CUESTION_PLANTEADA" in parsed.secciones
    assert "CONTESTACION_COMPLETA" in parsed.secciones
    # El cuerpo de la contestación debe contener el artículo discutido.
    assert "19.2.e)" in parsed.secciones["CONTESTACION_COMPLETA"]


def test_get_field_tolerante_acentos_y_alias() -> None:
    parsed = parse_consulta_html(V_IRPF)
    # Sin tildes y con alias debe encontrarlo.
    assert parsed.get_field("Num Consulta") is not None
    assert parsed.get_field("Numero de Consulta") is not None
    assert parsed.get_field("Núm. Consulta") is not None


def test_get_field_inexistente_devuelve_none() -> None:
    parsed = parse_consulta_html(V_IRPF)
    assert parsed.get_field("CampoQueNoExiste") is None


def test_parse_consulta_date_dd_mm_yyyy() -> None:
    assert parse_consulta_date("30/01/2024") == date(2024, 1, 30)
    assert parse_consulta_date("1 de marzo de 2024") == date(2024, 3, 1)
    assert parse_consulta_date("hoy") is None


def test_parse_acepta_texto_plano() -> None:
    raw = (
        "Núm. Consulta: V0123-24\n"
        "Fecha Salida: 30/01/2024\n"
        "Normativa: Ley 35/2006 art. 19\n"
        "\n"
        "Descripción Hechos:\n"
        "Texto del consultante.\n"
        "\n"
        "Cuestión Planteada:\n"
        "¿Es deducible?\n"
        "\n"
        "Contestación Completa:\n"
        "Sí, según el artículo X."
    )
    parsed = parse_consulta_html(raw)
    assert parsed.get_field("Núm. Consulta") == "V0123-24"
    assert "CONTESTACION_COMPLETA" in parsed.secciones


def test_parse_header_no_confunde_lineas_del_cuerpo() -> None:
    """Líneas como 'Descripción Hechos:' del cuerpo NO deben ir al header."""
    parsed = parse_consulta_html(V_IRPF)
    keys_norm = {k.lower() for k in parsed.header_fields}
    assert "descripcion hechos" not in keys_norm
    assert "cuestion planteada" not in keys_norm
