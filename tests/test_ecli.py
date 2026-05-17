"""Tests del parser de ECLI español."""

from __future__ import annotations

import pytest

from hacienda_ai.models import Organo
from hacienda_ai.rag.jurisprudence import (
    EcliParseError,
    organo_from_tribunal_codigo,
    parse_ecli,
)


def test_parse_ecli_ts_basico() -> None:
    e = parse_ecli("ECLI:ES:TS:2024:1234")
    assert e.tribunal_codigo == "TS"
    assert e.anyo == 2024
    assert e.id_interno == "1234"
    assert e.canonical == "ECLI:ES:TS:2024:1234"


def test_parse_ecli_acepta_minusculas_y_normaliza() -> None:
    e = parse_ecli("ecli:es:ts:2024:1234")
    assert e.tribunal_codigo == "TS"
    assert e.canonical == "ECLI:ES:TS:2024:1234"


def test_parse_ecli_tsj_madrid() -> None:
    e = parse_ecli("ECLI:ES:TSJM:2024:5678")
    assert e.tribunal_codigo == "TSJM"
    assert e.anyo == 2024
    assert organo_from_tribunal_codigo(e.tribunal_codigo) == Organo.TSJ


def test_parse_ecli_ap_madrid() -> None:
    e = parse_ecli("ECLI:ES:APM:2023:9876")
    assert e.tribunal_codigo == "APM"
    assert organo_from_tribunal_codigo(e.tribunal_codigo) == Organo.AP


def test_parse_ecli_audiencia_nacional() -> None:
    e = parse_ecli("ECLI:ES:AN:2024:567")
    assert e.tribunal_codigo == "AN"
    assert organo_from_tribunal_codigo(e.tribunal_codigo) == Organo.AN


def test_parse_ecli_constitucional() -> None:
    e = parse_ecli("ECLI:ES:TC:2023:123")
    assert e.tribunal_codigo == "TC"
    assert organo_from_tribunal_codigo(e.tribunal_codigo) == Organo.TC


def test_parse_ecli_con_sufijo_se_preserva_canonico() -> None:
    """`ECLI:ES:TS:2024:1234.S2` (sufijo opcional) sigue siendo válido."""
    e = parse_ecli("ECLI:ES:TS:2024:1234.S2")
    # El canónico no incluye el sufijo: es la forma estable.
    assert e.canonical == "ECLI:ES:TS:2024:1234"


def test_parse_ecli_invalido_pais() -> None:
    with pytest.raises(EcliParseError):
        parse_ecli("ECLI:FR:CC:2024:1234")  # Francia, no España.


def test_parse_ecli_invalido_formato() -> None:
    with pytest.raises(EcliParseError):
        parse_ecli("no es un ECLI")


def test_parse_ecli_vacio_lanza() -> None:
    with pytest.raises(EcliParseError):
        parse_ecli("")


def test_organo_from_tribunal_distingue_TS_y_TSJ() -> None:
    """Caso clásico: 'TS' no debe capturar 'TSJM' por prefijo."""
    assert organo_from_tribunal_codigo("TS") == Organo.TS
    assert organo_from_tribunal_codigo("TSJM") == Organo.TSJ
    assert organo_from_tribunal_codigo("TSJAND") == Organo.TSJ


def test_organo_codigo_desconocido_lanza() -> None:
    with pytest.raises(EcliParseError):
        organo_from_tribunal_codigo("XYZ")


def test_organo_codigo_vacio_lanza() -> None:
    with pytest.raises(EcliParseError):
        organo_from_tribunal_codigo("")
