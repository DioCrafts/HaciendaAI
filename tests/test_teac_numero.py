"""Tests del parser de número de reclamación TEAC/TEAR."""

from __future__ import annotations

import pytest

from hacienda_ai.rag.teac import (
    NumeroReclamacionParseError,
    parse_numero_reclamacion,
)


def test_parse_canonico_teac_central() -> None:
    n = parse_numero_reclamacion("00/12345/2023")
    assert n.codigo_tea == 0
    assert n.numero == 12345
    assert n.anyo == 2023
    assert n.canonical == "00/12345/2023"
    assert n.is_teac_central


def test_parse_canonico_tear_madrid() -> None:
    n = parse_numero_reclamacion("28/00345/2024")
    assert n.codigo_tea == 28
    assert n.numero == 345
    assert n.anyo == 2024
    # Padding a 5 dígitos.
    assert n.canonical == "28/00345/2024"
    assert not n.is_teac_central


def test_parse_con_sufijos_seccion_y_sub() -> None:
    n = parse_numero_reclamacion("00/12345/2023/04/01")
    assert n.seccion == 4
    assert n.subexpediente == 1
    assert n.canonical == "00/12345/2023/04/01"


def test_parse_forma_corta_RG_acepta() -> None:
    """R.G. (Registro General) forma corta de TEAC: se asume código 0."""
    n = parse_numero_reclamacion("R.G. 12345/2023")
    assert n.codigo_tea == 0
    assert n.canonical == "00/12345/2023"


def test_parse_forma_corta_RG_con_dos_puntos() -> None:
    n = parse_numero_reclamacion("R.G.: 12345/2023")
    assert n.canonical == "00/12345/2023"


def test_parse_forma_corta_RG_con_slash() -> None:
    n = parse_numero_reclamacion("RG/12345/2023")
    assert n.canonical == "00/12345/2023"


def test_parse_solo_numero_y_anyo_asume_teac() -> None:
    """`12345/2023` → asumimos TEAC central."""
    n = parse_numero_reclamacion("12345/2023")
    assert n.codigo_tea == 0
    assert n.canonical == "00/12345/2023"


def test_parse_anyo_corto_normaliza_a_4() -> None:
    n = parse_numero_reclamacion("00/12345/23")
    assert n.anyo == 2023
    assert n.canonical == "00/12345/2023"


def test_parse_numero_con_padding_se_normaliza() -> None:
    n = parse_numero_reclamacion("00/345/2023")
    # 345 se rellena a 00345 en canónico.
    assert n.canonical == "00/00345/2023"


def test_parse_invalido_lanza() -> None:
    with pytest.raises(NumeroReclamacionParseError):
        parse_numero_reclamacion("not a number")


def test_parse_vacio_lanza() -> None:
    with pytest.raises(NumeroReclamacionParseError):
        parse_numero_reclamacion("")
