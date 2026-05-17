"""Tests del parser HTML CENDOJ."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from hacienda_ai.rag.jurisprudence import parse_sentencia_html
from hacienda_ai.rag.jurisprudence.parser import (
    html_to_plain_text,
    parse_header,
    parse_sentencia_date,
    split_sections,
)

FIXTURES = Path(__file__).parent / "fixtures" / "cendoj"
TS_IRPF = (FIXTURES / "ECLI:ES:TS:2024:1234.html").read_text(encoding="utf-8")
AN_IVA = (FIXTURES / "ECLI:ES:AN:2024:567.html").read_text(encoding="utf-8")
TS_SOCIAL = (FIXTURES / "ECLI:ES:TS:2024:9999.html").read_text(encoding="utf-8")


def test_html_to_plain_text_quita_tags_y_decodifica_entidades() -> None:
    raw = "<p>Hola&nbsp;mundo<br>nueva l&iacute;nea</p>"
    plain = html_to_plain_text(raw)
    assert "<" not in plain and ">" not in plain
    # &nbsp; → espacio.
    assert "Hola mundo" in plain
    # El salto de línea por <br> se preserva.
    assert "\n" in plain


def test_parse_header_ts_irpf() -> None:
    plain = html_to_plain_text(TS_IRPF)
    header = parse_header(plain)
    assert header.get("ECLI") == "ECLI:ES:TS:2024:1234"
    assert "Tribunal Supremo" in header.get("Órgano", "")
    assert header.get("Sección") == "2"
    assert header.get("Fecha") == "15/06/2024"
    # Materia tributaria explícita en CENDOJ.
    assert "Materia" in header
    assert "IRPF" in header["Materia"] or "Renta" in header["Materia"]


def test_parse_header_an_iva() -> None:
    plain = html_to_plain_text(AN_IVA)
    header = parse_header(plain)
    assert header.get("ECLI") == "ECLI:ES:AN:2024:567"
    assert "Audiencia Nacional" in header.get("Órgano", "")
    assert header.get("Sección") == "4"


def test_split_sections_localiza_las_cinco_secciones_canonicas() -> None:
    parsed = parse_sentencia_html(TS_IRPF)
    # ENCABEZAMIENTO, ANTECEDENTES, FUNDAMENTOS, FALLO siempre están en una
    # sentencia normal. HECHOS_PROBADOS no aparece en contencioso-administrativo
    # (es típico de penal/social).
    assert "ENCABEZAMIENTO" in parsed.secciones
    assert "ANTECEDENTES_DE_HECHO" in parsed.secciones
    assert "FUNDAMENTOS_DE_DERECHO" in parsed.secciones
    assert "FALLO" in parsed.secciones
    # El cuerpo de FUNDAMENTOS debe contener al menos un FJ.
    assert "PRIMERO" in parsed.secciones["FUNDAMENTOS_DE_DERECHO"]


def test_parsed_sentencia_get_field_es_tolerante_a_acentos() -> None:
    parsed = parse_sentencia_html(TS_IRPF)
    # "Órgano" pedido sin tildes debe encontrarlo.
    assert parsed.get_field("Organo") is not None
    assert parsed.get_field("Órgano") is not None


def test_parsed_sentencia_get_field_devuelve_none_si_no_existe() -> None:
    parsed = parse_sentencia_html(TS_IRPF)
    assert parsed.get_field("CampoInventado") is None


def test_parse_sentencia_date_dd_mm_yyyy() -> None:
    assert parse_sentencia_date("15/06/2024") == date(2024, 6, 15)


def test_parse_sentencia_date_letra() -> None:
    assert parse_sentencia_date("15 de junio de 2024") == date(2024, 6, 15)


def test_parse_sentencia_date_acepta_mes_con_tilde() -> None:
    # Aunque en CENDOJ la fecha viene en formato numérico, el helper se
    # usa también para fechas extraídas del cuerpo.
    assert parse_sentencia_date("1 de febrero de 2024") == date(2024, 2, 1)


def test_parse_sentencia_date_invalida_devuelve_none() -> None:
    assert parse_sentencia_date("hoy") is None
    assert parse_sentencia_date("32/13/2024") is None


def test_parse_acepta_tambien_texto_plano_directo() -> None:
    """Algunos fixtures pueden ser texto sin tags HTML."""
    plain_text = (
        "ECLI: ECLI:ES:TS:2024:1234\n"
        "Fecha: 15/06/2024\n"
        "\n"
        "FUNDAMENTOS DE DERECHO\n"
        "PRIMERO. Lorem ipsum.\n"
        "\n"
        "FALLO\n"
        "DESESTIMAR el recurso."
    )
    parsed = parse_sentencia_html(plain_text)
    assert parsed.get_field("ECLI") == "ECLI:ES:TS:2024:1234"
    assert "FALLO" in parsed.secciones


def test_parse_header_NO_confunde_lineas_de_cuerpo_con_metadatos() -> None:
    """Una línea como 'PRIMERO: bla bla' del cuerpo NO debe ir a header_fields."""
    parsed = parse_sentencia_html(TS_IRPF)
    # "PRIMERO" no es campo canónico; no debe estar en el header.
    keys_norm = {k.lower() for k in parsed.header_fields}
    assert "primero" not in keys_norm


def test_split_sections_devuelve_dict_vacio_si_no_hay_encabezados() -> None:
    out = split_sections("texto sin encabezados canónicos")
    assert out == {}
