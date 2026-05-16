"""Tests del hasher de documentos BOE.

Verificaciones clave:

- El hash de un documento real es estable (regression test).
- El hash es robusto a reordenaciones triviales de espacios y a comentarios
  XML — pequeñas variaciones de formato del BOE no deben generar drift.
- El hash distingue documentos con contenido distinto.
- Documentos sin `<texto>` ni `<documento>` levantan `DocumentHashError`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hacienda_ai.rag.ingestion.boe_document import (
    DocumentHashError,
    extract_body,
    hash_document,
    normalize_body,
)

FIXTURES = Path(__file__).parent / "fixtures" / "boe"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_hash_documento_estable_sobre_fixture_real() -> None:
    xml = _load("documento_BOE-A-2024-1700.xml")
    digest, _ = hash_document(xml)
    # SHA-256 hex es 64 caracteres.
    assert len(digest) == 64
    # Determinismo: misma entrada, mismo hash.
    digest_2, _ = hash_document(xml)
    assert digest == digest_2


def test_hash_robusto_ante_whitespace_extra() -> None:
    xml = _load("documento_BOE-A-2024-1700.xml")
    digest_original, _ = hash_document(xml)
    # Insertamos saltos de línea y espacios extra entre tags. El normalizador
    # debe colapsarlos.
    xml_con_espacios = xml.replace("</p>", "</p>   \n  \n ").replace(
        "<p ", "  <p "
    )
    digest_alterado, _ = hash_document(xml_con_espacios)
    assert digest_original == digest_alterado


def test_hash_robusto_ante_comentarios_xml() -> None:
    xml = _load("documento_BOE-A-2024-1700.xml")
    digest_original, _ = hash_document(xml)
    # BOE a veces añade comentarios editoriales; no deben afectar el hash.
    xml_con_comentario = xml.replace(
        "<texto>",
        "<!-- nota editorial añadida posteriormente --><texto>",
    )
    digest_con_comentario, _ = hash_document(xml_con_comentario)
    assert digest_original == digest_con_comentario


def test_hash_distingue_documentos_con_contenido_diferente() -> None:
    xml1 = _load("documento_BOE-A-2024-1700.xml")
    # Alteramos el contenido del articulado: el hash debe cambiar.
    xml2 = xml1.replace("Anguila,", "Anguila, Andorra,")
    h1, _ = hash_document(xml1)
    h2, _ = hash_document(xml2)
    assert h1 != h2


def test_extract_body_levanta_si_no_hay_texto_ni_documento() -> None:
    with pytest.raises(DocumentHashError):
        extract_body("<sumario><meta/></sumario>")


def test_hash_normaliza_entidades_xml() -> None:
    # Dos formas válidas de escapar `&` en XML deben producir el mismo
    # texto plano (y por tanto el mismo hash). `&amp;` es la entidad
    # nombrada; `&#38;` es la referencia numérica.
    a = "<documento><texto><p>uno&amp;dos</p></texto></documento>"
    b = "<documento><texto><p>uno&#38;dos</p></texto></documento>"
    ha, ta = hash_document(a)
    hb, tb = hash_document(b)
    assert ta == tb == "uno&dos"
    assert ha == hb


def test_normalize_body_colapsa_whitespace() -> None:
    body = "<p>uno   dos\n\n  tres</p>"
    assert normalize_body(body) == "uno dos tres"


def test_hash_falla_con_documento_vacio() -> None:
    with pytest.raises(DocumentHashError):
        hash_document("<documento><texto>   </texto></documento>")
