"""Parser del sumario diario del BOE.

El BOE publica cada día un "sumario" que lista todas las disposiciones
de ese boletín, agrupadas por sección y departamento. La API de datos
abiertos lo expone en dos formatos:

- JSON (`Accept: application/json`): estructura anidada con `data.sumario`.
- XML (default): `<sumario>` con `<seccion>`, `<departamento>`, `<epigrafe>`,
  `<item>`.

Soportamos los dos para robustez: si la API devuelve JSON malformado en un
día concreto, el caller puede caer al XML. La salida es la misma:
`list[SummaryItem]`, una entrada por disposición publicada ese día.

Lo que NO hace este módulo:
- No descarga el documento (eso lo hace `boe_document.py`).
- No clasifica fiscal/no-fiscal (eso lo hace `tax_filter.py`).
- No habla con la red (recibe el payload ya descargado).

Mantenerlo HTTP-free lo hace trivialmente testeable contra fixtures.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from typing import Any


class SummaryParseError(ValueError):
    """Estructura del sumario inesperada o corrupta."""


@dataclass(frozen=True)
class SummaryItem:
    """Una disposición publicada en el sumario diario.

    Campos derivados directamente del sumario, sin enriquecer. Cualquier
    consumo posterior (clasificación fiscal, descarga del XML completo,
    construcción de `Norma`) parte de aquí.

    `url_xml` apunta al XML del documento publicado tal como apareció en
    el BOE de ese día — distinto del XML consolidado (`legislacion-consolidada`),
    que es el agregado vivo con todas las modificaciones aplicadas.
    """

    boe_id: str
    fecha_publicacion: date
    seccion_codigo: str
    seccion_nombre: str
    departamento: str
    epigrafe: str
    titulo: str
    url_pdf: str | None
    url_html: str | None
    url_xml: str | None


# ---------- Helpers de normalización tolerante ----------

# La API BOE colapsa listas de un solo elemento a objetos en JSON: si una
# sección solo tiene un departamento, `seccion.departamento` es dict en vez
# de list. Lo mismo para epígrafes e items. Estos helpers normalizan ambos
# casos antes de iterar.


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _get_text(value: Any) -> str:
    """Extrae texto de un campo que puede ser string o dict con clave de texto.

    El parser JSON del API BOE serializa elementos con atributos como
    objetos. La clave del texto visible varía entre `texto`, `#text` y
    `text` según el endpoint y la versión del API; probamos las tres en
    orden. Devolvemos cadena vacía si no hay nada legible.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("texto", "#text", "text"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return ""


# ---------- Parsing de fechas del sumario ----------


def _parse_fecha_publicacion(raw: str) -> date:
    """Acepta los formatos que aparecen en el sumario BOE: YYYYMMDD o DD/MM/YYYY.

    Lanza `SummaryParseError` si no encaja con ninguno — preferimos fallar
    aquí a aceptar una fecha errónea silenciosamente.
    """
    s = raw.strip()
    if re.fullmatch(r"\d{8}", s):
        return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    m = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", s)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    raise SummaryParseError(
        f"fecha_publicacion no parseable: {raw!r} (esperado YYYYMMDD o DD/MM/YYYY)"
    )


# ---------- JSON parser ----------


def _parse_json_summary(payload: str) -> list[SummaryItem]:
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise SummaryParseError("payload JSON no es un objeto raíz")

    sumario = (
        data.get("data", {}).get("sumario")
        if isinstance(data.get("data"), dict)
        else None
    )
    if sumario is None:
        # Algunos snapshots históricos no envuelven en `data`.
        sumario = data.get("sumario")
    if not isinstance(sumario, dict):
        raise SummaryParseError("no se encuentra `data.sumario` en el payload JSON")

    metadatos = sumario.get("metadatos") or {}
    fecha_raw = (
        _get_text(metadatos.get("fecha_publicacion"))
        or _get_text(metadatos.get("fechaInv"))
        or _get_text(metadatos.get("fecha"))
    )
    if not fecha_raw:
        raise SummaryParseError("metadatos sin fecha_publicacion")
    fecha = _parse_fecha_publicacion(fecha_raw)

    items: list[SummaryItem] = []
    for diario in _as_list(sumario.get("diario")):
        for seccion in _as_list(diario.get("seccion")):
            seccion_codigo = _get_text(seccion.get("codigo"))
            seccion_nombre = _get_text(seccion.get("nombre"))
            for departamento in _as_list(seccion.get("departamento")):
                dep_nombre = _get_text(departamento.get("nombre"))
                # El sumario puede tener:
                #   - departamento.epigrafe[*].item[*]   (con epígrafes)
                #   - departamento.item[*]               (sin epígrafes)
                # El segundo caso aparece para secciones de menor rango y
                # algunos boletines antiguos. Tratamos los dos.
                epigrafes = _as_list(departamento.get("epigrafe"))
                if epigrafes:
                    for epigrafe in epigrafes:
                        epi_nombre = _get_text(epigrafe.get("nombre"))
                        for item in _as_list(epigrafe.get("item")):
                            items.append(
                                _build_item(
                                    item,
                                    fecha=fecha,
                                    seccion_codigo=seccion_codigo,
                                    seccion_nombre=seccion_nombre,
                                    departamento=dep_nombre,
                                    epigrafe=epi_nombre,
                                )
                            )
                else:
                    for item in _as_list(departamento.get("item")):
                        items.append(
                            _build_item(
                                item,
                                fecha=fecha,
                                seccion_codigo=seccion_codigo,
                                seccion_nombre=seccion_nombre,
                                departamento=dep_nombre,
                                epigrafe="",
                            )
                        )
    return items


def _build_item(
    item: Any,
    fecha: date,
    seccion_codigo: str,
    seccion_nombre: str,
    departamento: str,
    epigrafe: str,
) -> SummaryItem:
    if not isinstance(item, dict):
        raise SummaryParseError(f"item no es objeto: {item!r}")
    boe_id = _get_text(item.get("identificador") or item.get("id"))
    titulo = _get_text(item.get("titulo"))
    if not boe_id or not titulo:
        raise SummaryParseError(
            f"item sin identificador o titulo: id={boe_id!r} titulo={titulo!r}"
        )
    return SummaryItem(
        boe_id=boe_id,
        fecha_publicacion=fecha,
        seccion_codigo=seccion_codigo,
        seccion_nombre=seccion_nombre,
        departamento=departamento,
        epigrafe=epigrafe,
        titulo=titulo,
        url_pdf=_get_text(item.get("url_pdf") or item.get("urlPdf")) or None,
        url_html=_get_text(item.get("url_html") or item.get("urlHtm")) or None,
        url_xml=_get_text(item.get("url_xml") or item.get("urlXml")) or None,
    )


# ---------- XML parser ----------


def _xml_text(element: ET.Element | None, tag: str) -> str:
    if element is None:
        return ""
    child = element.find(tag)
    if child is None or child.text is None:
        return ""
    return child.text.strip()


def _parse_xml_summary(payload: str) -> list[SummaryItem]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise SummaryParseError(f"XML del sumario malformado: {exc}") from exc

    meta = root.find("meta")
    if meta is None:
        # Algunos sumarios envuelven en <sumario><diario>... directamente
        # sin <meta>; usamos el atributo `fechaInv` si está.
        meta = root

    fecha_raw = (
        _xml_text(meta, "fechaInv")
        or _xml_text(meta, "fecha")
        or meta.get("fechaInv")
        or meta.get("fecha")
        or ""
    )
    if not fecha_raw:
        raise SummaryParseError("sumario XML sin meta.fechaInv ni meta.fecha")
    fecha = _parse_fecha_publicacion(fecha_raw)

    items: list[SummaryItem] = []
    for diario in root.iter("diario"):
        for seccion in diario.findall("seccion"):
            seccion_codigo = seccion.get("num") or seccion.get("codigo") or ""
            seccion_nombre = seccion.get("nombre") or ""
            for departamento in seccion.findall("departamento"):
                dep_nombre = departamento.get("nombre") or ""
                epigrafes = departamento.findall("epigrafe")
                if epigrafes:
                    for epigrafe in epigrafes:
                        epi_nombre = epigrafe.get("nombre") or ""
                        for item in epigrafe.findall("item"):
                            items.append(
                                _build_xml_item(
                                    item,
                                    fecha=fecha,
                                    seccion_codigo=seccion_codigo,
                                    seccion_nombre=seccion_nombre,
                                    departamento=dep_nombre,
                                    epigrafe=epi_nombre,
                                )
                            )
                else:
                    for item in departamento.findall("item"):
                        items.append(
                            _build_xml_item(
                                item,
                                fecha=fecha,
                                seccion_codigo=seccion_codigo,
                                seccion_nombre=seccion_nombre,
                                departamento=dep_nombre,
                                epigrafe="",
                            )
                        )
    return items


def _build_xml_item(
    item: ET.Element,
    fecha: date,
    seccion_codigo: str,
    seccion_nombre: str,
    departamento: str,
    epigrafe: str,
) -> SummaryItem:
    boe_id = item.get("id") or _xml_text(item, "identificador")
    titulo = _xml_text(item, "titulo")
    if not boe_id or not titulo:
        raise SummaryParseError(
            f"item XML sin id o titulo: id={boe_id!r} titulo={titulo!r}"
        )
    return SummaryItem(
        boe_id=boe_id,
        fecha_publicacion=fecha,
        seccion_codigo=seccion_codigo,
        seccion_nombre=seccion_nombre,
        departamento=departamento,
        epigrafe=epigrafe,
        titulo=titulo,
        url_pdf=_xml_text(item, "urlPdf") or None,
        url_html=_xml_text(item, "urlHtm") or None,
        url_xml=_xml_text(item, "urlXml") or None,
    )


# ---------- Entry point ----------


def parse_summary(payload: str, *, content_type: str | None = None) -> list[SummaryItem]:
    """Parsea el sumario BOE en formato JSON o XML.

    `content_type` se usa como hint si está disponible (HTTP header). Si no
    se pasa, se autodetecta probando JSON primero y XML como fallback.
    """
    payload_stripped = payload.lstrip()
    is_json_hint = content_type and "json" in content_type.lower()
    is_xml_hint = content_type and "xml" in content_type.lower()
    looks_json = payload_stripped.startswith("{")
    looks_xml = payload_stripped.startswith("<")

    if is_json_hint or (not is_xml_hint and looks_json):
        return _parse_json_summary(payload)
    if is_xml_hint or looks_xml:
        return _parse_xml_summary(payload)
    raise SummaryParseError(
        "no se reconoce el formato del sumario (ni JSON ni XML)"
    )
