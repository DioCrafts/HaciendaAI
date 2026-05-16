"""Tests de extractores de fallo y ratio decidendi."""

from __future__ import annotations

from pathlib import Path

from hacienda_ai.models import FalloSentido
from hacienda_ai.rag.jurisprudence import (
    extract_fallo,
    extract_ratio_decidendi,
    parse_sentencia_html,
)

FIXTURES = Path(__file__).parent / "fixtures" / "cendoj"
TS_IRPF = (FIXTURES / "ECLI:ES:TS:2024:1234.html").read_text(encoding="utf-8")
AN_IVA = (FIXTURES / "ECLI:ES:AN:2024:567.html").read_text(encoding="utf-8")


# ---------- extract_fallo ----------


def test_extract_fallo_desestimatoria_ts_irpf() -> None:
    parsed = parse_sentencia_html(TS_IRPF)
    sentido, texto = extract_fallo(
        parsed.plain_text, parsed.secciones.get("FALLO")
    )
    assert sentido == FalloSentido.DESESTIMATORIA
    assert "DESESTIMAR" in texto


def test_extract_fallo_estimatoria_parcial_an_iva() -> None:
    parsed = parse_sentencia_html(AN_IVA)
    sentido, texto = extract_fallo(
        parsed.plain_text, parsed.secciones.get("FALLO")
    )
    assert sentido == FalloSentido.ESTIMATORIA_PARCIAL
    assert texto  # no vacío.


def test_extract_fallo_distingue_parcial_de_total() -> None:
    """`ESTIMAR PARCIALMENTE` debe ganar a `ESTIMAR` por orden de patrones."""
    sentido, _ = extract_fallo(
        "FALLO\nESTIMAR PARCIALMENTE el recurso de casación.",
        "ESTIMAR PARCIALMENTE el recurso de casación.",
    )
    assert sentido == FalloSentido.ESTIMATORIA_PARCIAL


def test_extract_fallo_estimatoria_pura() -> None:
    sentido, _ = extract_fallo(
        "irrelevante",
        "Por todo ello, ESTIMAR el recurso de casación interpuesto.",
    )
    assert sentido == FalloSentido.ESTIMATORIA


def test_extract_fallo_inadmision() -> None:
    sentido, _ = extract_fallo(
        "irrelevante",
        "Inadmitir el recurso de casación por falta de interés casacional.",
    )
    assert sentido == FalloSentido.INADMISION


def test_extract_fallo_no_ha_lugar_es_desestimatoria() -> None:
    sentido, _ = extract_fallo(
        "irrelevante", "No ha lugar al recurso de casación interpuesto."
    )
    assert sentido == FalloSentido.DESESTIMATORIA


def test_extract_fallo_desconocido_sin_verbos() -> None:
    """Sin verbos canónicos, devolvemos `DESCONOCIDO` (no inventamos)."""
    sentido, _ = extract_fallo(
        "irrelevante", "Esta es una sección sin verbo de fallo claro."
    )
    assert sentido == FalloSentido.DESCONOCIDO


def test_extract_fallo_cae_al_plain_text_si_no_hay_seccion() -> None:
    plain = (
        "Antecedentes y fundamentos varios...\n"
        "F A L L O\n"
        "DESESTIMAR el recurso."
    )
    sentido, texto = extract_fallo(plain, None)
    assert sentido == FalloSentido.DESESTIMATORIA
    assert "DESESTIMAR" in texto


def test_extract_fallo_seccion_vacia_cae_a_plain_text() -> None:
    plain = "FALLAMOS\nESTIMAR el recurso."
    sentido, _ = extract_fallo(plain, "")
    assert sentido == FalloSentido.ESTIMATORIA


# ---------- extract_ratio_decidendi ----------


def test_ratio_decidendi_detecta_marcador_esta_sala_considera() -> None:
    parsed = parse_sentencia_html(TS_IRPF)
    ratio = extract_ratio_decidendi(
        parsed.plain_text,
        fundamentos_section=parsed.secciones.get("FUNDAMENTOS_DE_DERECHO"),
    )
    # El fixture tiene "Esta Sala considera..." en el FJ TERCERO.
    assert ratio is not None
    assert (
        "Esta Sala considera" in ratio
        or "doctrina" in ratio.lower()
        or "responderse" in ratio.lower()
    )


def test_ratio_decidendi_marcador_debe_responderse() -> None:
    text = (
        "PRIMERO. Antecedentes.\n\n"
        "SEGUNDO. Marco normativo.\n\n"
        "TERCERO. Doctrina aplicable.\n\n"
        "Debe responderse a la cuestión planteada que los gastos NO son "
        "deducibles del IRPF en estas circunstancias."
    )
    ratio = extract_ratio_decidendi(text)
    assert ratio is not None
    assert "Debe responderse" in ratio


def test_ratio_decidendi_se_fija_como_doctrina() -> None:
    text = (
        "FUNDAMENTOS DE DERECHO\n\n"
        "PRIMERO. Lorem.\n\n"
        "SEGUNDO. Se fija como doctrina jurisprudencial que cualquier X "
        "implica Y según el art. 19 LIRPF."
    )
    ratio = extract_ratio_decidendi(text)
    assert ratio is not None
    assert "fija como doctrina" in ratio


def test_ratio_decidendi_sin_marcadores_devuelve_ultimo_fj() -> None:
    text = (
        "PRIMERO. Antecedentes detallados del litigio.\n\n"
        "SEGUNDO. Análisis del marco normativo aplicable.\n\n"
        "TERCERO. Conclusión: el recurso debe ser desestimado por las razones "
        "expuestas en los fundamentos anteriores y el principio general del "
        "Derecho tributario aplicable al caso."
    )
    ratio = extract_ratio_decidendi(text)
    assert ratio is not None
    # Cae al último FJ (TERCERO).
    assert "Conclusión" in ratio or "desestimado" in ratio


def test_ratio_decidendi_texto_vacio_devuelve_none() -> None:
    assert extract_ratio_decidendi("") is None
    assert extract_ratio_decidendi("   \n\n  ") is None


def test_ratio_decidendi_trunca_a_1200_chars() -> None:
    huge = "Se fija como doctrina " + "x" * 5000
    ratio = extract_ratio_decidendi(huge)
    assert ratio is not None
    assert len(ratio) <= 1210  # 1200 + " […]".
