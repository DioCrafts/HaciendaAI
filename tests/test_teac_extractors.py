"""Tests de extractores TEAC: tipo, sentido, criterio, normativa."""

from __future__ import annotations

from pathlib import Path

from hacienda_ai.models import (
    Impuesto,
    SentidoResolucion,
    TipoResolucion,
)
from hacienda_ai.rag.teac import (
    detect_sentido,
    detect_tipo,
    extract_criterio,
    extract_normativa,
    parse_resolucion_html,
)
from hacienda_ai.rag.teac.extractors import detect_impuesto

FIXTURES = Path(__file__).parent / "fixtures" / "teac"
R_TEAC_UNIFICA = (FIXTURES / "00_12345_2023.html").read_text(encoding="utf-8")
R_TEAC_ORDINARIA = (FIXTURES / "00_67890_2022.html").read_text(encoding="utf-8")
R_TEAR_MADRID = (FIXTURES / "28_00345_2024.html").read_text(encoding="utf-8")


# ---------- detect_tipo ----------


def test_detect_tipo_unificacion_explicita() -> None:
    parsed = parse_resolucion_html(R_TEAC_UNIFICA)
    tipo = detect_tipo(
        tipo_header=parsed.get_field("Tipo de Resolución"),
        asunto=parsed.get_field("Materia"),
        cuerpo=parsed.plain_text,
    )
    assert tipo == TipoResolucion.UNIFICA_CRITERIO


def test_detect_tipo_ordinaria_si_no_hay_marcadores() -> None:
    parsed = parse_resolucion_html(R_TEAC_ORDINARIA)
    tipo = detect_tipo(
        tipo_header=parsed.get_field("Tipo de Resolución"),
        asunto=parsed.get_field("Materia"),
        cuerpo=parsed.plain_text,
    )
    assert tipo == TipoResolucion.ORDINARIA


def test_detect_tipo_extension_efectos() -> None:
    tipo = detect_tipo(
        tipo_header=None,
        asunto=None,
        cuerpo=(
            "El presente expediente trae causa de un recurso de extensión "
            "de efectos previsto en el artículo 244 LGT..."
        ),
    )
    assert tipo == TipoResolucion.EXTIENDE_EFECTOS


def test_detect_tipo_articulo_242_se_clasifica_como_unifica() -> None:
    tipo = detect_tipo(
        tipo_header=None,
        asunto=None,
        cuerpo=(
            "Conforme al artículo 242 de la Ley General Tributaria, se fija "
            "el criterio que vincula a la AEAT y a los TEAR..."
        ),
    )
    assert tipo == TipoResolucion.UNIFICA_CRITERIO


# ---------- detect_sentido ----------


def test_detect_sentido_desestimatoria() -> None:
    parsed = parse_resolucion_html(R_TEAC_UNIFICA)
    sentido = detect_sentido(parsed.plain_text, parsed.secciones.get("FALLO"))
    assert sentido == SentidoResolucion.DESESTIMATORIA


def test_detect_sentido_estimatoria_parcial() -> None:
    parsed = parse_resolucion_html(R_TEAC_ORDINARIA)
    sentido = detect_sentido(parsed.plain_text, parsed.secciones.get("FALLO"))
    assert sentido == SentidoResolucion.ESTIMATORIA_PARCIAL


def test_detect_sentido_tear_madrid_desestimatoria() -> None:
    parsed = parse_resolucion_html(R_TEAR_MADRID)
    sentido = detect_sentido(parsed.plain_text, parsed.secciones.get("FALLO"))
    assert sentido == SentidoResolucion.DESESTIMATORIA


def test_detect_sentido_inadmision() -> None:
    sentido = detect_sentido(
        "irrelevante", "INADMITIR por extemporaneidad la reclamación."
    )
    assert sentido == SentidoResolucion.INADMISION


def test_detect_sentido_retroaccion() -> None:
    sentido = detect_sentido(
        "irrelevante",
        "ORDENAR la retroacción de actuaciones para subsanar el defecto...",
    )
    assert sentido == SentidoResolucion.RETROACCION


def test_detect_sentido_desconocido_sin_verbos() -> None:
    sentido = detect_sentido("texto sin verbo", "")
    assert sentido == SentidoResolucion.DESCONOCIDO


def test_detect_sentido_parcial_antes_que_total() -> None:
    """Regresión: 'ESTIMAR PARCIALMENTE' no debe clasificarse como ESTIMATORIA."""
    sentido = detect_sentido(
        "fallo en texto plano",
        "ESTIMAR PARCIALMENTE la reclamación interpuesta.",
    )
    assert sentido == SentidoResolucion.ESTIMATORIA_PARCIAL


# ---------- detect_impuesto ----------


def test_detect_impuesto_irpf() -> None:
    parsed = parse_resolucion_html(R_TEAC_UNIFICA)
    impuesto = detect_impuesto(
        normativa=parsed.get_field("Normativa"),
        materia=parsed.get_field("Materia"),
        cuerpo=parsed.plain_text,
    )
    assert impuesto == Impuesto.IRPF


def test_detect_impuesto_iva() -> None:
    parsed = parse_resolucion_html(R_TEAC_ORDINARIA)
    impuesto = detect_impuesto(
        normativa=parsed.get_field("Normativa"),
        materia=parsed.get_field("Materia"),
        cuerpo=parsed.plain_text,
    )
    assert impuesto == Impuesto.IVA


def test_detect_impuesto_isd_en_tear() -> None:
    parsed = parse_resolucion_html(R_TEAR_MADRID)
    impuesto = detect_impuesto(
        normativa=parsed.get_field("Normativa"),
        materia=parsed.get_field("Materia"),
        cuerpo=parsed.plain_text,
    )
    assert impuesto == Impuesto.ISD


# ---------- extract_normativa ----------


def test_extract_normativa_combina_header_y_cuerpo() -> None:
    parsed = parse_resolucion_html(R_TEAC_UNIFICA)
    citas = extract_normativa(
        parsed.plain_text, parsed.get_field("Normativa")
    )
    joined = " ".join(citas).lower()
    assert "35/2006" in joined


def test_extract_normativa_vacio_si_no_hay() -> None:
    citas = extract_normativa("texto sin referencias", None)
    assert citas == ()


# ---------- extract_criterio ----------


def test_extract_criterio_usa_seccion_dedicada_cuando_existe() -> None:
    """Si el HTML expone bloque CRITERIO, se usa verbatim (gold)."""
    parsed = parse_resolucion_html(R_TEAC_UNIFICA)
    criterio = extract_criterio(
        parsed.plain_text,
        criterio_section=parsed.secciones.get("CRITERIO"),
        fundamentos_section=parsed.secciones.get("FUNDAMENTOS"),
    )
    assert criterio is not None
    # El fixture tiene el criterio sintetizado en la sección dedicada.
    assert "deducibles" in criterio.lower() or "vinculación" in criterio.lower()
    # No debe empezar con el literal "CRITERIO:" (se ha limpiado).
    assert not criterio.upper().startswith("CRITERIO")


def test_extract_criterio_marcador_este_tribunal() -> None:
    text = (
        "PRIMERO. Antecedentes.\n\n"
        "SEGUNDO. Análisis.\n\n"
        "TERCERO. Este Tribunal Central considera que los gastos NO son "
        "deducibles al amparo del art. 19.2.e) LIRPF."
    )
    criterio = extract_criterio(text, fundamentos_section=text)
    assert criterio is not None
    assert "Este Tribunal Central" in criterio


def test_extract_criterio_marcador_fija_el_criterio() -> None:
    text = (
        "FUNDAMENTOS\n\n"
        "PRIMERO. Marco normativo.\n\n"
        "SEGUNDO. Se fija el criterio de que el supuesto X tributa al "
        "tipo Y según el art. Z LIVA."
    )
    criterio = extract_criterio(text, fundamentos_section=text)
    assert criterio is not None
    assert "fija el criterio" in criterio


def test_extract_criterio_fallback_ultimo_parrafo() -> None:
    text = (
        "PRIMERO. Análisis normativo extenso de la cuestión planteada por "
        "el reclamante y de los preceptos aplicables al supuesto.\n\n"
        "SEGUNDO. Conclusión final: el supuesto encaja en la previsión "
        "normativa por concurrir todos los requisitos exigidos por el "
        "precepto, procediendo en consecuencia la estimación parcial de la "
        "reclamación interpuesta."
    )
    criterio = extract_criterio(text)
    assert criterio is not None
    # Cae al último FJ o al último párrafo.
    assert "Conclusión" in criterio or "estimación" in criterio


def test_extract_criterio_vacio_devuelve_none() -> None:
    assert extract_criterio("") is None
    assert extract_criterio("   \n\n") is None


def test_extract_criterio_trunca_a_1500_chars() -> None:
    text = "Se fija el criterio de que " + ("x " * 5000)
    criterio = extract_criterio(text)
    assert criterio is not None
    assert len(criterio) <= 1510
