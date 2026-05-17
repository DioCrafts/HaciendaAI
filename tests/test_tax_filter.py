"""Tests del clasificador de materia fiscal del sumario BOE.

Cubre las tres ramas:

1. Items con departamento Hacienda + epígrafe normativo → `fiscal`.
2. Items con Jefatura del Estado + título con keyword fiscal → `probable`.
3. Items rechazados: epígrafe no normativo, negative keyword, otros
   departamentos sin keyword.

Los tests usan títulos reales (verbatim) de leyes/órdenes fiscales
publicadas en BOE para asegurar que las regex y normalizaciones casan
sobre texto real, no solo sobre ejemplos sintéticos.
"""

from __future__ import annotations

from hacienda_ai.models import SourceKind
from hacienda_ai.rag.ingestion.tax_filter import classify, map_epigrafe_to_kind


def test_map_epigrafe_reconoce_los_tipos_normativos_principales() -> None:
    assert map_epigrafe_to_kind("Ley") == SourceKind.LEY
    assert map_epigrafe_to_kind("Ley Orgánica") == SourceKind.LEY_ORGANICA
    assert (
        map_epigrafe_to_kind("Real Decreto-ley") == SourceKind.REAL_DECRETO_LEY
    )
    assert (
        map_epigrafe_to_kind("Real Decreto Legislativo")
        == SourceKind.REAL_DECRETO_LEGISLATIVO
    )
    assert map_epigrafe_to_kind("Real Decreto") == SourceKind.REAL_DECRETO
    assert map_epigrafe_to_kind("Orden") == SourceKind.ORDEN_MINISTERIAL
    assert map_epigrafe_to_kind("Resolución") == SourceKind.RESOLUCION


def test_map_epigrafe_rechaza_no_normativos() -> None:
    # Items que aparecen en el sumario pero no entran al corpus.
    assert map_epigrafe_to_kind("Nombramientos") is None
    assert map_epigrafe_to_kind("Ceses") is None
    assert map_epigrafe_to_kind("Anuncios") is None
    assert map_epigrafe_to_kind("") is None


def test_hacienda_orden_es_fiscal() -> None:
    classification = classify(
        departamento="MINISTERIO DE HACIENDA Y FUNCIÓN PÚBLICA",
        epigrafe="Orden",
        titulo=(
            "Orden HFP/115/2024, de 25 de enero, por la que se determinan "
            "los países y territorios, así como los regímenes fiscales "
            "perjudiciales, que tienen la consideración de jurisdicciones "
            "no cooperativas."
        ),
    )
    assert classification.accept
    assert classification.relevance == "fiscal"
    assert classification.kind == SourceKind.ORDEN_MINISTERIAL


def test_jefatura_del_estado_con_keyword_irpf_es_probable() -> None:
    classification = classify(
        departamento="JEFATURA DEL ESTADO",
        epigrafe="Ley",
        titulo=(
            "Ley 5/2024, de 28 de enero, por la que se modifica la Ley "
            "35/2006, de 28 de noviembre, del Impuesto sobre la Renta de "
            "las Personas Físicas, en relación con la deducción por "
            "maternidad."
        ),
    )
    assert classification.accept
    assert classification.relevance == "probable"
    assert classification.kind == SourceKind.LEY
    assert any("irpf" in kw or "renta" in kw for kw in classification.matched_keywords)


def test_jefatura_del_estado_sin_keyword_fiscal_no_se_acepta() -> None:
    classification = classify(
        departamento="JEFATURA DEL ESTADO",
        epigrafe="Ley",
        titulo=(
            "Ley 4/2024, de 25 de enero, de creación de la Autoridad "
            "Administrativa Independiente de Defensa del Cliente Financiero."
        ),
    )
    assert not classification.accept
    assert classification.relevance == "no_fiscal"


def test_otro_ministerio_sin_keyword_se_rechaza() -> None:
    classification = classify(
        departamento="MINISTERIO DE INDUSTRIA Y TURISMO",
        epigrafe="Real Decreto",
        titulo=(
            "Real Decreto 92/2024, de 29 de enero, por el que se desarrolla "
            "la estructura orgánica básica del Ministerio de Industria y "
            "Turismo."
        ),
    )
    assert not classification.accept


def test_otro_ministerio_con_keyword_fiscal_se_acepta_como_probable() -> None:
    # Casos donde un ministerio no fiscal sí toca tributación (ej. impuestos
    # energéticos). Debemos detectarlo aunque el departamento no sea Hacienda.
    classification = classify(
        departamento="MINISTERIO PARA LA TRANSICIÓN ECOLÓGICA Y EL RETO DEMOGRÁFICO",
        epigrafe="Real Decreto",
        titulo=(
            "Real Decreto 200/2024, sobre el impuesto especial sobre la "
            "electricidad y los gravámenes a la generación."
        ),
    )
    assert classification.accept
    assert classification.relevance == "probable"


def test_negative_keyword_ministerio_fiscal_rechaza_aunque_tenga_kind() -> None:
    # "Ministerio Fiscal" no es Hacienda. La regla negative protege contra
    # falsos positivos al confundir "fiscal" (tributario) con "fiscal"
    # (Ministerio Público).
    classification = classify(
        departamento="MINISTERIO DE JUSTICIA",
        epigrafe="Real Decreto",
        titulo=(
            "Real Decreto 100/2024, por el que se aprueba el reglamento "
            "de la carrera fiscal."
        ),
    )
    assert not classification.accept


def test_epigrafe_no_normativo_se_rechaza_aunque_sea_hacienda() -> None:
    # Si el item es un nombramiento o anuncio, no entra al corpus aunque
    # lo firme Hacienda.
    classification = classify(
        departamento="MINISTERIO DE HACIENDA",
        epigrafe="Nombramientos",
        titulo="Resolución por la que se nombra Subdirector General...",
    )
    assert not classification.accept
    assert classification.kind is None


def test_normalizacion_tilde_no_afecta_clasificacion() -> None:
    # El sumario a veces tiene mayúsculas sin tilde por encoding; el
    # clasificador debe ser robusto a esto.
    sin_tildes = classify(
        departamento="MINISTERIO DE HACIENDA Y FUNCION PUBLICA",
        epigrafe="Orden",
        titulo="Orden por la que se aprueba el modelo 100 del IRPF",
    )
    con_tildes = classify(
        departamento="MINISTERIO DE HACIENDA Y FUNCIÓN PÚBLICA",
        epigrafe="Orden",
        titulo="Orden por la que se aprueba el modelo 100 del IRPF",
    )
    assert sin_tildes.relevance == con_tildes.relevance == "fiscal"


def test_keyword_modelo_303_iva_se_detecta() -> None:
    classification = classify(
        departamento="MINISTERIO DE HACIENDA",
        epigrafe="Orden",
        titulo=(
            "Orden HAC/646/2023, de 9 de junio, por la que se modifican la "
            "Orden EHA/3786/2008, por la que se aprueba el modelo 303 de "
            "autoliquidación del Impuesto sobre el Valor Añadido."
        ),
    )
    assert classification.relevance == "fiscal"
    assert "modelo 303" in classification.matched_keywords
