"""Tests del modelo `ResolucionTEAC`: from_dict / to_dict / validaciones."""

from __future__ import annotations

import pytest

from hacienda_ai.models import (
    CriterioConfidence,
    Impuesto,
    OrganoTEA,
    ResolucionTEAC,
    SentidoResolucion,
    TipoResolucion,
    ValidationError,
)

_HASH = "a" * 64


def _base_data() -> dict[str, object]:
    return {
        "numero": "00/12345/2023",
        "organo": "teac",
        "fecha": "2023-06-15",
        "tipo": "unifica_criterio",
        "sentido": "desestimatoria",
        "impuesto": "irpf",
        "asunto": "Gastos de defensa jurídica en IRPF",
        "criterio_confidence": "auto",
        "normativa": ["Ley 35/2006 art. 19.2.e)"],
        "resolucion_texto": "Texto completo de la resolución...",
        "content_hash": _HASH,
        "last_fetched_at": "2024-09-01",
    }


def test_from_dict_minimo() -> None:
    r = ResolucionTEAC.from_dict(_base_data())
    assert r.numero == "00/12345/2023"
    assert r.organo == OrganoTEA.TEAC
    assert r.tipo == TipoResolucion.UNIFICA_CRITERIO
    assert r.sentido == SentidoResolucion.DESESTIMATORIA
    assert r.impuesto == Impuesto.IRPF
    assert r.criterio_confidence == CriterioConfidence.AUTO
    assert r.normativa == ("Ley 35/2006 art. 19.2.e)",)
    assert r.sede is None
    assert r.criterio is None
    assert r.url is None


def test_to_dict_omite_opcionales_none() -> None:
    r = ResolucionTEAC.from_dict(_base_data())
    out = r.to_dict()
    for key in ("sede", "criterio", "url"):
        assert key not in out


def test_from_dict_completo() -> None:
    data = {
        **_base_data(),
        "sede": "Madrid",
        "criterio": "Este Tribunal Central considera que no son deducibles.",
        "url": "https://serviciostelematicos.minhap.gob.es/DYCTEA/...",
    }
    r = ResolucionTEAC.from_dict(data)
    assert r.sede == "Madrid"
    assert r.criterio is not None and "Tribunal Central" in r.criterio
    out = r.to_dict()
    assert out["sede"] == "Madrid"
    assert "criterio" in out
    assert "url" in out


def test_round_trip_estable() -> None:
    r1 = ResolucionTEAC.from_dict({**_base_data(), "criterio": "X", "sede": "Madrid"})
    r2 = ResolucionTEAC.from_dict(r1.to_dict())
    assert r1 == r2


def test_organo_desconocido_lanza() -> None:
    with pytest.raises(ValidationError):
        ResolucionTEAC.from_dict({**_base_data(), "organo": "xxx"})


def test_tipo_desconocido_lanza() -> None:
    with pytest.raises(ValidationError):
        ResolucionTEAC.from_dict({**_base_data(), "tipo": "xxx"})


def test_sentido_desconocido_lanza() -> None:
    with pytest.raises(ValidationError):
        ResolucionTEAC.from_dict({**_base_data(), "sentido": "xxx"})


def test_impuesto_desconocido_lanza() -> None:
    with pytest.raises(ValidationError):
        ResolucionTEAC.from_dict({**_base_data(), "impuesto": "xxx"})


def test_content_hash_invalido_lanza() -> None:
    with pytest.raises(ValidationError):
        ResolucionTEAC.from_dict({**_base_data(), "content_hash": "no"})


def test_normativa_no_lista_lanza() -> None:
    with pytest.raises(ValidationError):
        ResolucionTEAC.from_dict({**_base_data(), "normativa": "Ley 35/2006"})


def test_falta_campo_obligatorio_lanza() -> None:
    data = _base_data()
    del data["fecha"]
    with pytest.raises(ValidationError):
        ResolucionTEAC.from_dict(data)
