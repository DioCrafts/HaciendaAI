"""Tests del modelo `ConsultaDGT`: from_dict / to_dict / validaciones."""

from __future__ import annotations

from datetime import date

import pytest

from hacienda_ai.models import (
    ConsultaDGT,
    CriterioConfidence,
    Impuesto,
    ValidationError,
)

_HASH = "a" * 64


def _base_data() -> dict[str, object]:
    return {
        "numero": "V0123-24",
        "fecha_salida": "2024-01-30",
        "impuesto": "irpf",
        "asunto": "Gastos de defensa jurídica en IRPF",
        "cuestion_planteada": "Si los gastos son deducibles...",
        "contestacion_completa": "La normativa establece...",
        "criterio_confidence": "auto",
        "normativa": ["Ley 35/2006 art. 19.2.e)"],
        "content_hash": _HASH,
        "last_fetched_at": "2024-09-01",
    }


def test_from_dict_minimo() -> None:
    c = ConsultaDGT.from_dict(_base_data())
    assert c.numero == "V0123-24"
    assert c.fecha_salida == date(2024, 1, 30)
    assert c.impuesto == Impuesto.IRPF
    assert c.criterio_confidence == CriterioConfidence.AUTO
    assert c.normativa == ("Ley 35/2006 art. 19.2.e)",)
    assert c.fecha_entrada is None
    assert c.criterio is None
    assert c.url is None


def test_to_dict_omite_opcionales_none() -> None:
    c = ConsultaDGT.from_dict(_base_data())
    out = c.to_dict()
    for key in ("fecha_entrada", "criterio", "url"):
        assert key not in out


def test_from_dict_completo() -> None:
    data = {
        **_base_data(),
        "fecha_entrada": "2023-11-15",
        "criterio": "Esta DG considera que no son deducibles.",
        "url": "https://petete.tributos.hacienda.gob.es/...",
    }
    c = ConsultaDGT.from_dict(data)
    assert c.fecha_entrada == date(2023, 11, 15)
    assert c.criterio == "Esta DG considera que no son deducibles."
    assert c.url is not None
    out = c.to_dict()
    assert out["fecha_entrada"] == "2023-11-15"
    assert "criterio" in out
    assert "url" in out


def test_round_trip_estable() -> None:
    c1 = ConsultaDGT.from_dict({**_base_data(), "criterio": "X"})
    c2 = ConsultaDGT.from_dict(c1.to_dict())
    assert c1 == c2


def test_impuesto_desconocido_lanza() -> None:
    with pytest.raises(ValidationError):
        ConsultaDGT.from_dict({**_base_data(), "impuesto": "xxx"})


def test_criterio_confidence_invalida_lanza() -> None:
    with pytest.raises(ValidationError):
        ConsultaDGT.from_dict(
            {**_base_data(), "criterio_confidence": "xxx"}
        )


def test_content_hash_invalido_lanza() -> None:
    with pytest.raises(ValidationError):
        ConsultaDGT.from_dict({**_base_data(), "content_hash": "no-hash"})


def test_normativa_no_lista_lanza() -> None:
    with pytest.raises(ValidationError):
        ConsultaDGT.from_dict({**_base_data(), "normativa": "Ley 35/2006"})


def test_falta_campo_obligatorio_lanza() -> None:
    data = _base_data()
    del data["fecha_salida"]
    with pytest.raises(ValidationError):
        ConsultaDGT.from_dict(data)
