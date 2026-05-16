"""Tests del builder Norma + VersionNorma.

Verifica las dos decisiones automáticas críticas:

- `enacted_at`: regex sobre el título "de DD de mes de YYYY". Fallback a
  fecha de publicación si la regex falla.
- `effective_from`: día siguiente a la publicación (regla supletoria art.
  2.1 CC). Documentado en `notes` para que el revisor lo verifique contra
  la DF de la norma.

Y los invariantes:
- `Norma.kind` toma el valor de la clasificación.
- `VersionNorma.status` arranca siempre como VIGENTE.
- `VersionNorma.content_hash` se guarda verbatim (en minúsculas hex).
"""

from __future__ import annotations

from datetime import date

from hacienda_ai.models import NormaStatus, SourceKind
from hacienda_ai.rag.ingestion.boe_summary import SummaryItem
from hacienda_ai.rag.ingestion.norma_builder import (
    build_norma,
    parse_enacted_at_from_title,
)
from hacienda_ai.rag.ingestion.tax_filter import Classification


def _item(titulo: str, **overrides: object) -> SummaryItem:
    base = dict(
        boe_id="BOE-A-2024-1700",
        fecha_publicacion=date(2024, 1, 30),
        seccion_codigo="I",
        seccion_nombre="Disposiciones generales",
        departamento="MINISTERIO DE HACIENDA Y FUNCIÓN PÚBLICA",
        epigrafe="Orden",
        titulo=titulo,
        url_pdf=None,
        url_html=None,
        url_xml=None,
    )
    base.update(overrides)
    return SummaryItem(**base)  # type: ignore[arg-type]


_VALID_HASH = "a" * 64


def test_parse_enacted_at_extrae_fecha_del_titulo_de_ley() -> None:
    fecha = parse_enacted_at_from_title(
        "Ley 5/2024, de 28 de enero, por la que se modifica la Ley 35/2006",
        fallback=date(2024, 1, 30),
    )
    assert fecha == date(2024, 1, 28)


def test_parse_enacted_at_acepta_meses_con_tilde() -> None:
    fecha = parse_enacted_at_from_title(
        "Real Decreto 50/2024, de 1 de febrero, por el que se aprueba...",
        fallback=date(2024, 2, 5),
    )
    assert fecha == date(2024, 2, 1)


def test_parse_enacted_at_fallback_si_titulo_sin_fecha() -> None:
    fallback = date(2024, 6, 15)
    fecha = parse_enacted_at_from_title(
        "Resolución de la Dirección General de Tributos",
        fallback=fallback,
    )
    assert fecha == fallback


def test_parse_enacted_at_fallback_si_mes_invalido() -> None:
    fallback = date(2024, 7, 1)
    fecha = parse_enacted_at_from_title(
        "Ley 1/2024, de 1 de mayolio de 2024",  # mes inexistente
        fallback=fallback,
    )
    assert fecha == fallback


def test_build_norma_crea_norma_y_version_con_campos_correctos() -> None:
    classification = Classification(
        relevance="fiscal",
        kind=SourceKind.ORDEN_MINISTERIAL,
        matched_keywords=("tributari", "fiscal"),
        reasons=("departamento fiscal",),
    )
    item = _item(
        "Orden HFP/115/2024, de 25 de enero, por la que se determinan los "
        "países y territorios, así como los regímenes fiscales perjudiciales, "
        "que tienen la consideración de jurisdicciones no cooperativas."
    )
    built = build_norma(item, classification=classification, content_hash=_VALID_HASH)

    assert built.norma.boe_id == "BOE-A-2024-1700"
    assert built.norma.kind == SourceKind.ORDEN_MINISTERIAL
    assert built.norma.title == item.titulo
    # `enacted_at` se extrae del título "de 25 de enero".
    assert built.norma.enacted_at == date(2024, 1, 25)

    # `effective_from` = día siguiente a publicación.
    assert built.version.effective_from == date(2024, 1, 31)
    assert built.version.effective_to is None
    assert built.version.status == NormaStatus.VIGENTE
    assert built.version.content_hash == _VALID_HASH
    assert built.version.modified_by_boe_id is None
    assert built.version.notes is not None
    # Las notas dejan rastro auditable de la ingesta y avisan al revisor.
    assert "Ingestada automáticamente" in built.version.notes
    assert "effective_from" in built.version.notes


def test_build_norma_propaga_relevance_y_keywords_a_notes() -> None:
    classification = Classification(
        relevance="probable",
        kind=SourceKind.LEY,
        matched_keywords=("irpf", "renta de las personas fisicas"),
        reasons=("jefatura del estado",),
    )
    item = _item(
        "Ley 5/2024, de 28 de enero, por la que se modifica la Ley 35/2006",
        boe_id="BOE-A-2024-1699",
        departamento="JEFATURA DEL ESTADO",
        epigrafe="Ley",
    )
    built = build_norma(item, classification=classification, content_hash=_VALID_HASH)
    assert built.version.notes is not None
    assert "probable" in built.version.notes
    assert "irpf" in built.version.notes
