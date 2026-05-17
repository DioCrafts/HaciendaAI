"""Tests del modelo `ManualChunk`."""

from __future__ import annotations

import pytest

from hacienda_ai.models import (
    ManualChunk,
    ManualFuente,
    ValidationError,
)

_HASH = "a" * 64


def _base_data() -> dict[str, object]:
    return {
        "chunk_id": "manual_irpf::2024::cap1::sec1_1::sub1_1_1::p1of1",
        "fuente": "manual_irpf",
        "titulo": "1.1.1. Definición legal",
        "contenido": "Tienen la consideración de rendimientos del trabajo...",
        "content_hash": _HASH,
        "last_fetched_at": "2024-09-01",
    }


def test_from_dict_minimo() -> None:
    c = ManualChunk.from_dict(_base_data())
    assert c.chunk_id == "manual_irpf::2024::cap1::sec1_1::sub1_1_1::p1of1"
    assert c.fuente == ManualFuente.MANUAL_IRPF
    assert c.ejercicio is None
    assert c.capitulo is None
    assert c.referencias_normativas == ()


def test_to_dict_omite_opcionales_none() -> None:
    c = ManualChunk.from_dict(_base_data())
    out = c.to_dict()
    for key in (
        "ejercicio",
        "capitulo",
        "seccion",
        "subseccion",
        "page_inicio",
        "page_fin",
        "url_fuente",
    ):
        assert key not in out


def test_from_dict_completo() -> None:
    data = {
        **_base_data(),
        "ejercicio": 2024,
        "capitulo": "Capítulo 1. Rendimientos del trabajo",
        "seccion": "1.1. Concepto",
        "subseccion": "1.1.1. Definición legal",
        "page_inicio": 12,
        "page_fin": 15,
        "url_fuente": "https://sede.agenciatributaria.gob.es/...",
        "referencias_normativas": ["Ley 35/2006 art. 17", "Ley 35/2006 art. 19"],
    }
    c = ManualChunk.from_dict(data)
    assert c.ejercicio == 2024
    assert c.page_inicio == 12
    assert c.page_fin == 15
    assert c.referencias_normativas == (
        "Ley 35/2006 art. 17",
        "Ley 35/2006 art. 19",
    )


def test_round_trip_estable() -> None:
    c1 = ManualChunk.from_dict({**_base_data(), "ejercicio": 2024})
    c2 = ManualChunk.from_dict(c1.to_dict())
    assert c1 == c2


def test_fuente_desconocida_lanza() -> None:
    with pytest.raises(ValidationError):
        ManualChunk.from_dict({**_base_data(), "fuente": "xxx"})


def test_content_hash_invalido_lanza() -> None:
    with pytest.raises(ValidationError):
        ManualChunk.from_dict({**_base_data(), "content_hash": "no"})


def test_referencias_no_lista_lanza() -> None:
    with pytest.raises(ValidationError):
        ManualChunk.from_dict({**_base_data(), "referencias_normativas": "x"})


def test_ejercicio_no_int_lanza() -> None:
    with pytest.raises(ValidationError):
        ManualChunk.from_dict({**_base_data(), "ejercicio": "2024"})


def test_page_inicio_no_int_lanza() -> None:
    with pytest.raises(ValidationError):
        ManualChunk.from_dict({**_base_data(), "page_inicio": "primera"})


def test_falta_campo_obligatorio_lanza() -> None:
    data = _base_data()
    del data["contenido"]
    with pytest.raises(ValidationError):
        ManualChunk.from_dict(data)
