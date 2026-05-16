"""Tests del parser HTML del buscador DYCTEA."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from hacienda_ai.rag.teac import parse_resolucion_html
from hacienda_ai.rag.teac.parser import parse_resolucion_date

FIXTURES = Path(__file__).parent / "fixtures" / "teac"
R_TEAC_UNIFICA = (FIXTURES / "00_12345_2023.html").read_text(encoding="utf-8")
R_TEAC_ORDINARIA = (FIXTURES / "00_67890_2022.html").read_text(encoding="utf-8")
R_TEAR_MADRID = (FIXTURES / "28_00345_2024.html").read_text(encoding="utf-8")


def test_parse_header_teac_unifica() -> None:
    parsed = parse_resolucion_html(R_TEAC_UNIFICA)
    h = parsed.header_fields
    assert "00/12345/2023" in (parsed.get_field("Nº Resolución") or "")
    assert "15/06/2023" in (parsed.get_field("Fecha") or "")
    # El tipo de resolución viene declarado: "Unificación de criterio".
    tipo_field = parsed.get_field("Tipo de Resolución") or ""
    assert "Unificación" in tipo_field or "unificacion" in tipo_field.lower()
    # Unidad Resolutoria menciona "Central".
    unidad = parsed.get_field("Unidad Resolutoria") or ""
    assert "Central" in unidad
    # No se cuela nada del cuerpo en el header.
    assert not any("antecedentes" in k.lower() for k in h)


def test_parse_header_tear_madrid() -> None:
    parsed = parse_resolucion_html(R_TEAR_MADRID)
    assert "28/00345/2024" in (parsed.get_field("Nº Resolución") or "")
    # Unidad: "Regional de Madrid".
    unidad = parsed.get_field("Unidad Resolutoria") or ""
    assert "Regional" in unidad


def test_split_sections_localiza_criterio_y_fallo() -> None:
    parsed = parse_resolucion_html(R_TEAC_UNIFICA)
    assert "CRITERIO" in parsed.secciones
    assert "ANTECEDENTES" in parsed.secciones
    assert "FUNDAMENTOS" in parsed.secciones
    assert "FALLO" in parsed.secciones
    # El contenido de CRITERIO menciona la doctrina central.
    assert "deducibles" in parsed.secciones["CRITERIO"].lower()


def test_get_field_tolerante_alias() -> None:
    parsed = parse_resolucion_html(R_TEAC_UNIFICA)
    # Distintas formas de pedir el número de resolución deben encontrar el campo.
    assert parsed.get_field("N Resolucion") is not None
    assert parsed.get_field("Numero de Resolucion") is not None
    assert parsed.get_field("Nº Resolución") is not None


def test_get_field_inexistente_devuelve_none() -> None:
    parsed = parse_resolucion_html(R_TEAC_UNIFICA)
    assert parsed.get_field("CampoQueNoExiste") is None


def test_parse_consulta_date_dd_mm_yyyy() -> None:
    assert parse_resolucion_date("15/06/2023") == date(2023, 6, 15)
    assert parse_resolucion_date("1 de marzo de 2024") == date(2024, 3, 1)
    assert parse_resolucion_date("no es fecha") is None


def test_parse_acepta_texto_plano() -> None:
    raw = (
        "Nº Resolución: 00/12345/2023\n"
        "Fecha: 15/06/2023\n"
        "Tipo de Resolución: Unificación de criterio\n"
        "Unidad Resolutoria: Tribunal Económico-Administrativo Central\n"
        "\n"
        "CRITERIO:\n"
        "Los gastos NO son deducibles.\n"
        "\n"
        "ANTECEDENTES DE HECHO:\n"
        "PRIMERO. Texto.\n"
        "\n"
        "FUNDAMENTOS DE DERECHO:\n"
        "PRIMERO. Análisis.\n"
        "\n"
        "POR TODO LO EXPUESTO:\n"
        "DESESTIMAR la reclamación."
    )
    parsed = parse_resolucion_html(raw)
    assert parsed.get_field("Nº Resolución") == "00/12345/2023"
    assert "CRITERIO" in parsed.secciones
    assert "FALLO" in parsed.secciones


def test_parse_header_no_confunde_lineas_cuerpo() -> None:
    """Líneas como 'PRIMERO. Texto' del cuerpo NO deben ir al header."""
    parsed = parse_resolucion_html(R_TEAC_UNIFICA)
    keys_norm = {k.lower() for k in parsed.header_fields}
    assert "primero" not in keys_norm
    assert "criterio" not in keys_norm  # CRITERIO va a secciones, no a header.
