"""Tests del modelo `Sentencia`: from_dict / to_dict / validaciones."""

from __future__ import annotations

from datetime import date

import pytest

from hacienda_ai.models import (
    FalloSentido,
    Organo,
    RatioConfidence,
    Sentencia,
    ValidationError,
)

_HASH = "a" * 64


def _base_data() -> dict[str, object]:
    return {
        "ecli": "ECLI:ES:TS:2024:1234",
        "organo": "ts",
        "tribunal_codigo": "TS",
        "fecha": "2024-06-15",
        "fallo_sentido": "desestimatoria",
        "fallo_texto": "DESESTIMAR el recurso de casación interpuesto...",
        "ratio_confidence": "auto",
        "content_hash": _HASH,
        "last_fetched_at": "2024-09-01",
    }


def test_from_dict_minimo_valido() -> None:
    s = Sentencia.from_dict(_base_data())
    assert s.ecli == "ECLI:ES:TS:2024:1234"
    assert s.organo == Organo.TS
    assert s.tribunal_codigo == "TS"
    assert s.fecha == date(2024, 6, 15)
    assert s.fallo_sentido == FalloSentido.DESESTIMATORIA
    assert s.ratio_confidence == RatioConfidence.AUTO
    assert s.last_fetched_at == date(2024, 9, 1)


def test_to_dict_omite_campos_none() -> None:
    data = _base_data()
    s = Sentencia.from_dict(data)
    out = s.to_dict()
    # Sin valores opcionales → no aparecen en el dict serializado.
    for optional_key in (
        "sala",
        "seccion",
        "ponente",
        "numero_resolucion",
        "numero_recurso",
        "ratio_decidendi",
        "resumen",
        "url",
    ):
        assert optional_key not in out


def test_from_dict_con_campos_opcionales() -> None:
    data = {
        **_base_data(),
        "sala": "Tercera",
        "seccion": "2",
        "ponente": "M. APELLIDOS",
        "numero_resolucion": "890/2024",
        "numero_recurso": "4567/2022",
        "ratio_decidendi": "Esta Sala considera que los gastos de defensa...",
        "resumen": "IRPF — gastos deducibles",
        "url": "https://www.poderjudicial.es/...",
    }
    s = Sentencia.from_dict(data)
    assert s.sala == "Tercera"
    assert s.seccion == "2"
    assert s.ponente == "M. APELLIDOS"
    assert s.ratio_decidendi is not None and "gastos de defensa" in s.ratio_decidendi
    out = s.to_dict()
    assert out["sala"] == "Tercera"
    assert out["ratio_decidendi"].startswith("Esta Sala")


def test_round_trip_es_estable() -> None:
    data = {
        **_base_data(),
        "sala": "Cuarta",
        "ratio_decidendi": "doctrina X",
    }
    s1 = Sentencia.from_dict(data)
    s2 = Sentencia.from_dict(s1.to_dict())
    assert s1 == s2


def test_organo_desconocido_lanza() -> None:
    data = {**_base_data(), "organo": "xxx"}
    with pytest.raises(ValidationError):
        Sentencia.from_dict(data)


def test_fallo_sentido_desconocido_lanza() -> None:
    data = {**_base_data(), "fallo_sentido": "xxx"}
    with pytest.raises(ValidationError):
        Sentencia.from_dict(data)


def test_ratio_confidence_desconocida_lanza() -> None:
    data = {**_base_data(), "ratio_confidence": "xxx"}
    with pytest.raises(ValidationError):
        Sentencia.from_dict(data)


def test_content_hash_invalido_lanza() -> None:
    data = {**_base_data(), "content_hash": "no-es-sha256"}
    with pytest.raises(ValidationError):
        Sentencia.from_dict(data)


def test_falta_campo_obligatorio_lanza() -> None:
    data = _base_data()
    del data["fecha"]
    with pytest.raises(ValidationError):
        Sentencia.from_dict(data)
