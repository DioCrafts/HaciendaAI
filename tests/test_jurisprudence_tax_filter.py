"""Tests del filtro de materia tributaria sobre sentencias parseadas."""

from __future__ import annotations

from pathlib import Path

from hacienda_ai.rag.jurisprudence import (
    classify_sentencia,
    parse_sentencia_html,
)

FIXTURES = Path(__file__).parent / "fixtures" / "cendoj"
TS_IRPF = (FIXTURES / "ECLI:ES:TS:2024:1234.html").read_text(encoding="utf-8")
AN_IVA = (FIXTURES / "ECLI:ES:AN:2024:567.html").read_text(encoding="utf-8")
TS_SOCIAL = (FIXTURES / "ECLI:ES:TS:2024:9999.html").read_text(encoding="utf-8")


def test_ts_irpf_es_fiscal_por_materia_explicita() -> None:
    classification = classify_sentencia(parse_sentencia_html(TS_IRPF))
    assert classification.accept
    assert classification.relevance == "fiscal"
    # La materia del fixture menciona IRPF/Renta.
    assert classification.matched_keywords  # no vacío.


def test_an_iva_es_fiscal_por_materia_explicita() -> None:
    classification = classify_sentencia(parse_sentencia_html(AN_IVA))
    assert classification.accept
    assert classification.relevance == "fiscal"


def test_ts_social_se_rechaza() -> None:
    classification = classify_sentencia(parse_sentencia_html(TS_SOCIAL))
    assert not classification.accept
    assert classification.relevance == "no_fiscal"


def test_acronimo_iva_no_matchea_administrativa() -> None:
    """Regresión: `administrativa` contiene `iva` como substring; el
    word-boundary evita ese falso positivo."""
    text = (
        "Roj: STS X/2024\n"
        "ECLI: ECLI:ES:TS:2024:XX\n"
        "Órgano: Tribunal Supremo. Sala de lo Contencioso\n"
        "Sección: 5\n"
        "Fecha: 01/01/2024\n"
        "\n"
        "FUNDAMENTOS DE DERECHO\n"
        "PRIMERO. La autoridad administrativa competente para el "
        "procedimiento sancionador es la Comisión Nacional...\n"
        "\n"
        "FALLO\n"
        "DESESTIMAR el recurso."
    )
    classification = classify_sentencia(parse_sentencia_html(text))
    assert classification.relevance == "no_fiscal"


def test_sala_3a_seccion_2a_TS_es_fiscal_aunque_falte_materia() -> None:
    """La Sala 3ª Sección 2ª del TS es la sala fiscal: aceptar por defecto."""
    text = (
        "ECLI: ECLI:ES:TS:2024:99\n"
        "Órgano: Tribunal Supremo. Sala Tercera\n"
        "Sección: Segunda\n"
        "Fecha: 01/01/2024\n"
        "\n"
        "FUNDAMENTOS DE DERECHO\n"
        "PRIMERO. Lorem ipsum sin vocabulario tributario explícito.\n"
        "\n"
        "FALLO\n"
        "ESTIMAR el recurso."
    )
    classification = classify_sentencia(parse_sentencia_html(text))
    assert classification.accept
    assert classification.relevance == "fiscal"


def test_keyword_fuerte_en_cuerpo_da_probable() -> None:
    text = (
        "ECLI: ECLI:ES:TSJM:2024:99\n"
        "Órgano: Tribunal Superior de Justicia de Madrid\n"
        "Sección: 9\n"
        "Fecha: 01/01/2024\n"
        "Materia: Contencioso-administrativo\n"
        "\n"
        "FUNDAMENTOS DE DERECHO\n"
        "PRIMERO. La Agencia Estatal de Administración Tributaria practicó "
        "una liquidación tributaria al recurrente regularizando los "
        "rendimientos del trabajo declarados...\n"
        "\n"
        "FALLO\n"
        "ESTIMAR el recurso."
    )
    classification = classify_sentencia(parse_sentencia_html(text))
    # Materia genérica no fiscal, pero keywords fuertes en cuerpo → probable.
    assert classification.accept
    assert classification.relevance == "probable"


def test_sin_senales_es_no_fiscal() -> None:
    text = (
        "ECLI: ECLI:ES:AP:2024:99\n"
        "Órgano: Audiencia Provincial de Madrid\n"
        "Sección: 2\n"
        "Fecha: 01/01/2024\n"
        "Materia: Civil - Contratos\n"
        "\n"
        "FUNDAMENTOS DE DERECHO\n"
        "PRIMERO. El contrato de compraventa quedó perfeccionado...\n"
        "\n"
        "FALLO\n"
        "DESESTIMAR el recurso."
    )
    classification = classify_sentencia(parse_sentencia_html(text))
    assert not classification.accept
