"""Tests de extractores DGT: criterio, normativa, detect_impuesto."""

from __future__ import annotations

from pathlib import Path

from hacienda_ai.models import Impuesto
from hacienda_ai.rag.dgt import (
    detect_impuesto,
    extract_criterio,
    extract_normativa,
    parse_consulta_html,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dgt"
V_IRPF = (FIXTURES / "V0123-24.html").read_text(encoding="utf-8")
V_IVA = (FIXTURES / "V0456-24.html").read_text(encoding="utf-8")
V_IS = (FIXTURES / "V0789-24.html").read_text(encoding="utf-8")


# ---------- detect_impuesto ----------


def test_detect_impuesto_irpf_por_normativa() -> None:
    parsed = parse_consulta_html(V_IRPF)
    impuesto = detect_impuesto(
        normativa=parsed.get_field("Normativa"),
        asunto=parsed.get_field("Materia"),
        cuerpo=parsed.plain_text,
    )
    assert impuesto == Impuesto.IRPF


def test_detect_impuesto_iva_por_normativa() -> None:
    parsed = parse_consulta_html(V_IVA)
    impuesto = detect_impuesto(
        normativa=parsed.get_field("Normativa"),
        asunto=parsed.get_field("Materia"),
        cuerpo=parsed.plain_text,
    )
    assert impuesto == Impuesto.IVA


def test_detect_impuesto_is_por_normativa() -> None:
    parsed = parse_consulta_html(V_IS)
    impuesto = detect_impuesto(
        normativa=parsed.get_field("Normativa"),
        asunto=parsed.get_field("Materia"),
        cuerpo=parsed.plain_text,
    )
    assert impuesto == Impuesto.IS


def test_detect_impuesto_irnr_prioritario_sobre_irpf() -> None:
    """`IRNR` (no residentes) debe ganar a `IRPF` cuando ambos aparecen."""
    impuesto = detect_impuesto(
        normativa="Ley 35/2006, Ley 5/2004 sobre no residentes",
        asunto="Tributación de no residentes",
        cuerpo="...",
    )
    assert impuesto == Impuesto.IRNR


def test_detect_impuesto_acronimo_iva_no_matchea_administrativa() -> None:
    """Regresión: `administrativa` no debe clasificarse como IVA."""
    impuesto = detect_impuesto(
        normativa=None,
        asunto=None,
        cuerpo="resolución administrativa de la AEAT",
    )
    assert impuesto != Impuesto.IVA


def test_detect_impuesto_otro_si_no_hay_senales() -> None:
    impuesto = detect_impuesto(
        normativa=None,
        asunto="Cuestión genérica",
        cuerpo="Texto sin vocabulario tributario explícito.",
    )
    assert impuesto == Impuesto.OTRO


# ---------- extract_normativa ----------


def test_extract_normativa_combina_header_y_cuerpo() -> None:
    parsed = parse_consulta_html(V_IRPF)
    citas = extract_normativa(
        parsed.plain_text, parsed.get_field("Normativa")
    )
    # Al menos la cita de la cabecera debe aparecer.
    joined = " ".join(citas).lower()
    assert "35/2006" in joined or "ley 35/2006" in joined


def test_extract_normativa_deduplica() -> None:
    # Mismo texto repetido: no debe duplicar.
    citas = extract_normativa(
        "Ley 35/2006 art. 19 dice X. Ley 35/2006 art. 19 dice X.",
        normativa_header="Ley 35/2006 art. 19",
    )
    # Solo una entrada distinguible.
    lowered = [c.lower() for c in citas]
    assert len(lowered) == len(set(lowered))


def test_extract_normativa_detecta_alias_LIRPF() -> None:
    citas = extract_normativa(
        "Conforme al art. 7 LIRPF, los rendimientos exentos...",
        normativa_header=None,
    )
    assert any("LIRPF" in c.upper() for c in citas)


def test_extract_normativa_vacio_si_no_hay_nada() -> None:
    citas = extract_normativa("texto sin referencias normativas", None)
    assert citas == ()


# ---------- extract_criterio ----------


def test_extract_criterio_irpf_detecta_marcador_en_consecuencia() -> None:
    parsed = parse_consulta_html(V_IRPF)
    criterio = extract_criterio(
        parsed.plain_text,
        contestacion_section=parsed.secciones.get("CONTESTACION_COMPLETA"),
    )
    assert criterio is not None
    # El fixture tiene "En consecuencia, esta Dirección General considera..."
    assert (
        "consecuencia" in criterio.lower()
        or "Dirección General" in criterio
    )


def test_extract_criterio_iva_detecta_marcador() -> None:
    parsed = parse_consulta_html(V_IVA)
    criterio = extract_criterio(
        parsed.plain_text,
        contestacion_section=parsed.secciones.get("CONTESTACION_COMPLETA"),
    )
    assert criterio is not None
    assert len(criterio) > 50


def test_extract_criterio_se_concluye_que() -> None:
    text = (
        "Análisis técnico inicial.\n\n"
        "Se concluye que la operación queda exenta del impuesto al "
        "cumplirse los requisitos del art. 25 LIVA."
    )
    criterio = extract_criterio(text)
    assert criterio is not None
    assert "Se concluye" in criterio


def test_extract_criterio_por_tanto() -> None:
    text = (
        "Considerando los hechos.\n\n"
        "Por tanto, esta Dirección General concluye que el sujeto pasivo "
        "es el adquirente conforme al art. 84 LIVA."
    )
    criterio = extract_criterio(text)
    assert criterio is not None
    assert "Por tanto" in criterio


def test_extract_criterio_fallback_ultimo_parrafo() -> None:
    text = (
        "PRIMERO. Análisis normativo extenso del artículo aplicable y de "
        "sus desarrollos reglamentarios pertinentes para el caso planteado "
        "por el consultante.\n\n"
        "SEGUNDO. Conclusión final del análisis del precepto y su aplicación "
        "concreta al supuesto descrito en la presente consulta vinculante."
    )
    criterio = extract_criterio(text)
    assert criterio is not None
    assert "SEGUNDO" in criterio or "Conclusión" in criterio


def test_extract_criterio_vacio_devuelve_none() -> None:
    assert extract_criterio("") is None
    assert extract_criterio("   \n\n") is None


def test_extract_criterio_trunca_a_1500_chars() -> None:
    huge = "En consecuencia, " + "x" * 5000
    criterio = extract_criterio(huge)
    assert criterio is not None
    assert len(criterio) <= 1510  # 1500 + " […]".
