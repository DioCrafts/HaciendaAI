"""Tests del parser de número de consulta DGT."""

from __future__ import annotations

import pytest

from hacienda_ai.rag.dgt import (
    NumeroConsultaParseError,
    parse_numero_consulta,
)


def test_parse_canonical_form_short_year() -> None:
    n = parse_numero_consulta("V0123-24")
    assert n.numero == 123
    assert n.anyo == 2024
    assert n.canonical == "V0123-24"
    assert n.long_form == "V0123-2024"


def test_parse_long_form_full_year() -> None:
    n = parse_numero_consulta("V0123-2024")
    assert n.numero == 123
    assert n.anyo == 2024
    # Tras normalización, la forma canónica es la corta.
    assert n.canonical == "V0123-24"


def test_parse_numero_grande_padea_a_cuatro() -> None:
    n = parse_numero_consulta("V12-23")
    assert n.canonical == "V0012-23"


def test_parse_numero_de_cinco_digitos() -> None:
    # Hay años con más de 9999 consultas (raro pero posible).
    n = parse_numero_consulta("V12345-23")
    assert n.numero == 12345
    # El padding a 4 dígitos no recorta: el formato es ≥4 dígitos.
    assert n.canonical == "V12345-23"


def test_parse_acepta_espacios_internos() -> None:
    n = parse_numero_consulta("V 0123 - 24")
    assert n.canonical == "V0123-24"


def test_parse_minusculas_se_normaliza() -> None:
    n = parse_numero_consulta("v0123-24")
    assert n.canonical == "V0123-24"


def test_parse_consulta_no_vinculante_rechaza() -> None:
    """Las consultas C* (no vinculantes) NO entran a este corpus."""
    with pytest.raises(NumeroConsultaParseError) as exc_info:
        parse_numero_consulta("C0001-24")
    assert "NO vinculante" in str(exc_info.value)


def test_parse_vacio_lanza() -> None:
    with pytest.raises(NumeroConsultaParseError):
        parse_numero_consulta("")


def test_parse_sin_sufijo_lanza() -> None:
    with pytest.raises(NumeroConsultaParseError):
        parse_numero_consulta("V0123")


def test_parse_formato_invalido_lanza() -> None:
    with pytest.raises(NumeroConsultaParseError):
        parse_numero_consulta("foobar")
