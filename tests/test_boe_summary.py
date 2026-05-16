"""Tests del parser del sumario BOE.

Cubre los dos formatos (JSON y XML) contra fixtures con la estructura real
del API de datos abiertos. Los fixtures viven en `tests/fixtures/boe/`.

Decisiones de diseño que estos tests bloquean:

- El parser tolera listas de un solo elemento serializadas como dict
  (`seccion: {...}` en vez de `seccion: [{...}]`).
- El parser extrae correctamente la fecha del bloque `metadatos` en JSON
  y del atributo/elemento `fechaInv` en XML.
- Departamentos sin epígrafes (`departamento.item[*]` directamente) se
  procesan también.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from hacienda_ai.rag.ingestion.boe_summary import (
    SummaryParseError,
    parse_summary,
)

FIXTURES = Path(__file__).parent / "fixtures" / "boe"


def _load(filename: str) -> str:
    return (FIXTURES / filename).read_text(encoding="utf-8")


def test_parsea_sumario_json_real() -> None:
    items = parse_summary(_load("sumario_20240130.json"), content_type="application/json")

    # 4 items: Ley IRPF, Orden HFP, RD Industria, Nombramiento Justicia.
    assert len(items) == 4
    ids = {it.boe_id for it in items}
    assert ids == {
        "BOE-A-2024-1699",
        "BOE-A-2024-1700",
        "BOE-A-2024-1701",
        "BOE-A-2024-1702",
    }

    fecha = items[0].fecha_publicacion
    assert fecha == date(2024, 1, 30)
    # Todos los items comparten la misma fecha de publicación.
    assert all(it.fecha_publicacion == fecha for it in items)


def test_parsea_sumario_json_preserva_departamento_y_epigrafe() -> None:
    items = parse_summary(_load("sumario_20240130.json"))
    by_id = {it.boe_id: it for it in items}

    ley = by_id["BOE-A-2024-1699"]
    assert ley.departamento == "JEFATURA DEL ESTADO"
    assert ley.epigrafe == "Ley"
    assert ley.seccion_codigo == "I"

    orden = by_id["BOE-A-2024-1700"]
    assert orden.departamento == "MINISTERIO DE HACIENDA Y FUNCIÓN PÚBLICA"
    assert orden.epigrafe == "Orden"

    nombramiento = by_id["BOE-A-2024-1702"]
    assert nombramiento.seccion_codigo == "II"
    assert nombramiento.epigrafe == "Nombramientos"


def test_parsea_sumario_json_extrae_url_xml_string_directo() -> None:
    items = parse_summary(_load("sumario_20240130.json"))
    by_id = {it.boe_id: it for it in items}
    # En el sumario JSON real `url_xml` es un string plano, no un dict.
    assert by_id["BOE-A-2024-1700"].url_xml == (
        "https://www.boe.es/diario_boe/xml.php?id=BOE-A-2024-1700"
    )


def test_parsea_sumario_xml_real() -> None:
    items = parse_summary(_load("sumario_20240128.xml"), content_type="application/xml")
    assert len(items) == 1
    item = items[0]
    assert item.boe_id == "BOE-A-2024-1500"
    assert item.fecha_publicacion == date(2024, 1, 28)
    assert item.epigrafe == "Resolución"
    assert "modelo 720" in item.titulo
    assert item.url_xml == "/diario_boe/xml.php?id=BOE-A-2024-1500"


def test_parse_autodetecta_formato_sin_content_type() -> None:
    # Sin pasar content_type, el parser decide por el primer carácter.
    items_json = parse_summary(_load("sumario_20240130.json"))
    items_xml = parse_summary(_load("sumario_20240128.xml"))
    assert len(items_json) == 4
    assert len(items_xml) == 1


def test_parse_falla_con_payload_invalido() -> None:
    with pytest.raises(SummaryParseError):
        parse_summary("esto no es JSON ni XML")


def test_parse_falla_con_json_sin_sumario() -> None:
    with pytest.raises(SummaryParseError):
        parse_summary('{"data": {}}', content_type="application/json")
