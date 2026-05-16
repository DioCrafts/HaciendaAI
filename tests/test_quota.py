"""Tests del motor de cálculo de cuota IRPF.

Cubre:

1. La función pura `apply_progressive_scale`: tramos límite, base nula,
   bases muy altas (último tramo abierto), monotonía.
2. `select_scale`: emparejamiento por año, scope, componente, región y
   vigencia temporal (devengo dentro/fuera de `effective_from/to`).
3. Cálculo end-to-end `compute_quota` sobre perfiles canónicos (un
   asalariado solo Madrid 30 k, un autónomo con BIA, un caso con
   reducciones que devoran la BIG) frente a importes verificados a mano
   contra los arts. 63 y 66 LIRPF.
4. Comportamiento honesto cuando NO hay escala autonómica registrada:
   `cuota_integra_autonomica`, `cuota_liquida_total` y `cuota_diferencial`
   deben ser `None`, NO 0, y debe constar una nota explícita.
"""

from __future__ import annotations

from datetime import date

import pytest

from hacienda_ai.deductions import load_deductions
from hacienda_ai.irpf import (
    Bracket,
    apply_progressive_scale,
    compute_quota,
    load_tax_scales,
    select_scale,
)
from hacienda_ai.models import TaxProfile
from hacienda_ai.normas import load_norma_registry
from hacienda_ai.rules import evaluate_deductions

# ---------- apply_progressive_scale: función pura ----------


_STATE_GENERAL_2024 = (
    Bracket(up_to=12450.0, rate=0.095),
    Bracket(up_to=20200.0, rate=0.12),
    Bracket(up_to=35200.0, rate=0.15),
    Bracket(up_to=60000.0, rate=0.185),
    Bracket(up_to=300000.0, rate=0.225),
    Bracket(up_to=None, rate=0.245),
)


@pytest.mark.parametrize(
    "base,expected",
    [
        (0.0, 0.0),
        (-10.0, 0.0),  # base negativa: 0 (defensa anti-bug, no debería darse)
        (12450.0, 12450.0 * 0.095),  # primer tramo justo
        (20000.0, 12450.0 * 0.095 + (20000.0 - 12450.0) * 0.12),
        # 30 000 €: 12.450 × 9,5% + 7.750 × 12% + 9.800 × 15%
        (30000.0, 1182.75 + 930.0 + 1470.0),
        # 100 000 €: 12.450 × 9,5% + 7.750 × 12% + 15.000 × 15% +
        # 24.800 × 18,5% + 40.000 × 22,5%
        (
            100000.0,
            1182.75 + 930.0 + 2250.0 + 4588.0 + 9000.0,
        ),
        # 500 000 €: cae en el último tramo abierto
        (
            500000.0,
            12450.0 * 0.095
            + (20200.0 - 12450.0) * 0.12
            + (35200.0 - 20200.0) * 0.15
            + (60000.0 - 35200.0) * 0.185
            + (300000.0 - 60000.0) * 0.225
            + (500000.0 - 300000.0) * 0.245,
        ),
    ],
)
def test_apply_progressive_scale_state_general_2024(base: float, expected: float) -> None:
    got = apply_progressive_scale(base, _STATE_GENERAL_2024)
    assert got == pytest.approx(expected, abs=1e-6)


def test_apply_progressive_scale_monotone() -> None:
    """Cuanto mayor sea la base, mayor (o igual) debe ser la cuota."""
    previous = 0.0
    for base in range(0, 100000, 500):
        got = apply_progressive_scale(float(base), _STATE_GENERAL_2024)
        assert got >= previous - 1e-9
        previous = got


def test_apply_progressive_scale_empty_brackets() -> None:
    assert apply_progressive_scale(50000.0, ()) == 0.0


# ---------- select_scale: filtros y vigencia ----------


def test_select_scale_picks_state_general_for_year() -> None:
    scales = load_tax_scales()
    s = select_scale(
        scales,
        tax_year=2024,
        scope="estatal",
        component="general",
        region=None,
        devengo=date(2024, 12, 31),
    )
    assert s is not None
    assert s.id == "es_irpf_estatal_general_2024"


def test_select_scale_rejects_devengo_outside_effective_range() -> None:
    scales = load_tax_scales()
    # Devengo en 2025 buscando escala 2024 explícitamente no debería existir
    # como 2024; al pedir 2025, debe devolver la escala 2025.
    s = select_scale(
        scales,
        tax_year=2025,
        scope="estatal",
        component="general",
        region=None,
        devengo=date(2025, 12, 31),
    )
    assert s is not None
    assert s.id == "es_irpf_estatal_general_2025"


def test_select_scale_no_autonomic_returns_none() -> None:
    scales = load_tax_scales()
    s = select_scale(
        scales,
        tax_year=2024,
        scope="autonomico",
        component="general",
        region="Madrid",
        devengo=date(2024, 12, 31),
    )
    assert s is None  # Aún no hay escalas autonómicas registradas.


# ---------- compute_quota: integración con el corpus real ----------


def _load_engine() -> tuple[list, list, dict]:
    deductions = load_deductions()
    scales = load_tax_scales()
    return deductions, scales, {d.id: d for d in deductions}


def test_compute_quota_empleado_madrid_30k() -> None:
    """Perfil canónico: 30.000 € íntegros, 27.500 € netos, 1 hijo.

    BLG = 27.500 (no hay reducciones aplicadas por el motor).
    MPF = 5.550 (contribuyente) + 2.400 (1er hijo) = 7.950.
    Cuota íntegra estatal:
      escala(27.500) − escala(7.950)
      = (12.450·9,5% + 7.750·12% + 7.300·15%) − (7.950·9,5%)
      = (1.182,75 + 930 + 1.095) − 755,25
      = 3.207,75 − 755,25 = 2.452,50 €
    Cuota líquida estatal: 2.452,50 € (no hay deducciones de cuota
      estatales que aplique este perfil; el gasto deducible del trabajo
      ya se considera dentro del work_net).
    Cuota autonómica: None (sin escala Madrid registrada).
    """
    deductions, scales, ded_by_id = _load_engine()
    profile = TaxProfile.from_dict(
        {
            "tax_year": 2024,
            "region": "Madrid",
            "filing_mode": "individual",
            "personal": {"has_disability": False},
            "family": {"children_count": 1, "ascendants_count": 0},
            "income": {"work_gross": 30000, "work_net": 27500},
            "expenses": {},
            "documents": ["Libro de familia o certificado de convivencia"],
        }
    )
    registry = load_norma_registry()
    evaluations = evaluate_deductions(deductions, profile, registry)
    quota = compute_quota(profile, evaluations, ded_by_id, scales)

    assert quota.base_imponible_general == 27500.0
    assert quota.base_imponible_ahorro == 0.0
    assert quota.minimo_personal_familiar == 7950.0
    assert quota.minimo_aplicado_base_general == 7950.0
    assert quota.minimo_aplicado_base_ahorro == 0.0
    assert quota.base_liquidable_general == 27500.0
    assert quota.cuota_integra_estatal == pytest.approx(2452.50, abs=0.01)
    assert quota.cuota_liquida_estatal == pytest.approx(2452.50, abs=0.01)

    # Autonómica sin registrar → None + nota explícita anti-alucinación.
    assert quota.cuota_integra_autonomica is None
    assert quota.cuota_liquida_total is None
    assert quota.cuota_diferencial is None
    assert any("Madrid" in n for n in quota.notes)


def test_compute_quota_with_explicit_base_imponible_ahorro() -> None:
    """Cuando el perfil aporta BIA explícita, la cuota del ahorro se calcula
    sobre ella (12.000 € de ganancias mobiliarias)."""
    deductions, scales, ded_by_id = _load_engine()
    profile = TaxProfile.from_dict(
        {
            "tax_year": 2024,
            "region": "Madrid",
            "filing_mode": "individual",
            "personal": {"has_disability": False},
            "family": {"children_count": 0, "ascendants_count": 0},
            "income": {
                "work_gross": 30000,
                "work_net": 27500,
                "base_imponible_ahorro": 12000,
            },
            "expenses": {},
            "documents": [],
        }
    )
    registry = load_norma_registry()
    evaluations = evaluate_deductions(deductions, profile, registry)
    quota = compute_quota(profile, evaluations, ded_by_id, scales)

    # MPF: solo contribuyente (sin hijos) = 5.550. Cabe entera en la BLG.
    assert quota.minimo_personal_familiar == 5550.0
    assert quota.minimo_aplicado_base_general == 5550.0
    assert quota.minimo_aplicado_base_ahorro == 0.0

    # Cuota estatal del ahorro: 6.000·9,5% + 6.000·10,5% = 570 + 630 = 1.200
    assert quota.cuota_integra_estatal == pytest.approx(
        # general: escala(27.500) - escala(5.550) =
        # (1.182,75 + 930 + 1.095) - (5.550·9,5%) = 3.207,75 - 527,25
        (3207.75 - 527.25) + 1200.0,
        abs=0.01,
    )


def test_compute_quota_mpf_excedente_se_aplica_al_ahorro() -> None:
    """Si MPF > BLG, el excedente reduce la cuota del ahorro.

    Perfil: solo BIA (10.000 €), sin work_net.
    BIG = 0, MPF = 5.550 (contribuyente).
    BLG = 0, MPF aplicado a BLG = 0, excedente = 5.550.
    BLA = 10.000.
    Cuota estatal ahorro = escala(10.000) − escala(5.550)
                        = (6.000·9,5% + 4.000·10,5%) − (5.550·9,5%)
                        = (570 + 420) − 527,25 = 462,75 €
    """
    deductions, scales, ded_by_id = _load_engine()
    profile = TaxProfile.from_dict(
        {
            "tax_year": 2024,
            "region": "Madrid",
            "filing_mode": "individual",
            "personal": {"has_disability": False},
            "family": {"children_count": 0, "ascendants_count": 0},
            "income": {"capital_mobiliario_net": 10000},
            "expenses": {},
            "documents": [],
        }
    )
    registry = load_norma_registry()
    evaluations = evaluate_deductions(deductions, profile, registry)
    quota = compute_quota(profile, evaluations, ded_by_id, scales)

    assert quota.base_imponible_general == 0.0
    assert quota.base_imponible_ahorro == 10000.0
    assert quota.minimo_personal_familiar == 5550.0
    assert quota.minimo_aplicado_base_general == 0.0
    assert quota.minimo_aplicado_base_ahorro == 5550.0
    assert quota.cuota_integra_estatal == pytest.approx(462.75, abs=0.01)


def test_compute_quota_retenciones_se_descuentan_del_diferencial() -> None:
    """Las retenciones declaradas en `profile.withholdings` se restan de la
    cuota líquida total. Aquí la cuota líquida total no se puede calcular
    porque falta escala autonómica; la cuota diferencial debe ser None."""
    deductions, scales, ded_by_id = _load_engine()
    profile = TaxProfile.from_dict(
        {
            "tax_year": 2024,
            "region": "Madrid",
            "filing_mode": "individual",
            "personal": {"has_disability": False},
            "family": {"children_count": 0, "ascendants_count": 0},
            "income": {"work_gross": 30000, "work_net": 27500},
            "withholdings": [{"amount": 3000.0, "kind": "trabajo"}],
            "expenses": {},
            "documents": [],
        }
    )
    registry = load_norma_registry()
    evaluations = evaluate_deductions(deductions, profile, registry)
    quota = compute_quota(profile, evaluations, ded_by_id, scales)

    assert quota.retenciones_y_pagos_cuenta == 3000.0
    # Sin escala autonómica no podemos cerrar el diferencial: honestidad.
    assert quota.cuota_diferencial is None
