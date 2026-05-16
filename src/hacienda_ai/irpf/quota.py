"""Cálculo determinista de la cuota IRPF a partir del perfil y las deducciones.

Flujo (arts. 15, 50, 56–67, 74–77 y 79 LIRPF):

1. **Bases imponibles**: se toman del perfil si vienen explícitas
   (`income.base_imponible_general` / `..._ahorro`); si no, se intenta
   derivarlas de los rendimientos netos ya cargados.
2. **Reducciones** (categoría `reduccion`, estado `applies`): se restan a la
   BIG hasta dejarla en 0; el exceso se aplica a la BIA. Resultado: BLG/BLA.
3. **Mínimo personal y familiar**: suma de las deducciones `applies` con
   categoría `minimo_personal_familiar`. Se aplica primero contra la BLG y,
   si queda excedente, contra la BLA — tal como funciona el mecanismo
   estatal de tributación al tipo 0 del MPF.
4. **Cuota íntegra estatal**: `escala_estatal_general(BLG) − escala_estatal_general(MPF∩BLG)`
   más `escala_estatal_ahorro(BLA) − escala_estatal_ahorro(MPF excedente)`.
5. **Cuota íntegra autonómica**: idéntico pero con la escala autonómica
   correspondiente a `region` + `tax_year`. La parte autonómica del ahorro
   reutiliza la escala estatal del ahorro (la fija el Estado vía art. 76).
   Si no hay escala autonómica registrada, la cuota autonómica queda como
   `None` con nota explícita en `notes` — anti-alucinación.
6. **Deducciones de la cuota**: las de scope `estatal` se reparten al 50%
   entre la cuota líquida estatal y la autonómica (art. 67 + 77); las de
   scope `autonomico`, solo a la autonómica.
7. **Cuota diferencial**: cuota líquida total menos retenciones y pagos a
   cuenta (la lista `withholdings` del perfil, sumando el campo `amount` de
   cada entrada). Las deducciones de la cuota diferencial (maternidad,
   familia numerosa, ascendientes/descendientes con discapacidad: arts. 81 y
   81 bis LIRPF) se identifican por categoría/ID y se restan aquí, NO en la
   cuota líquida.

Limitaciones honestas del MVP:

- La derivación automática de las bases imponibles cubre los componentes más
  habituales (trabajo neto, capital inmobiliario neto, actividades
  económicas, imputaciones; capital mobiliario y ganancias para el ahorro).
  Para perfiles complejos (alteraciones patrimoniales con plazos, rentas
  exentas con progresividad, regímenes especiales) es preferible que el
  perfil aporte `base_imponible_general` y `..._ahorro` ya calculadas.
- La cuota autonómica solo se computa si existe escala registrada para
  la `region` + `tax_year`. Cualquier otro resultado sería inventarse
  números, lo que el proyecto evita por diseño.
- Las deducciones de cuota diferencial se identifican por una lista
  conservadora de IDs y por el patrón de nombre. Esto es heurístico hasta
  que el modelo `Deduction` incorpore un campo `cuota_aplicacion`
  (estructural vs. diferencial).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ..models import (
    Deduction,
    DeductionCategory,
    RuleEvaluation,
    Scope,
    Source,
    TaxProfile,
)
from .scales import Bracket, TaxScale, select_scale

# Las deducciones que se restan de la cuota diferencial (no de la cuota
# líquida) se identifican por su artículo (81 y 81 bis LIRPF) o por
# coincidencia con esta lista de IDs conocidos. Es heurístico hasta que el
# modelo `Deduction` lleve un campo dedicado.
_DIFERENCIAL_ARTICLE_PREFIXES = ("art. 81",)


def _is_cuota_diferencial(deduction: Deduction) -> bool:
    for source in deduction.sources:
        if source.article is None:
            continue
        article_normalized = source.article.lower().strip()
        for prefix in _DIFERENCIAL_ARTICLE_PREFIXES:
            if article_normalized.startswith(prefix.lower()):
                return True
    return False


def apply_progressive_scale(base: float, brackets: tuple[Bracket, ...]) -> float:
    """Aplica una escala progresiva por tramos.

    Cada tramo cubre la porción de la base entre el `up_to` del tramo
    anterior y el suyo; se multiplica por `rate`. El último tramo puede
    tener `up_to=None` para indicar "sin techo".
    """
    if base <= 0 or not brackets:
        return 0.0
    total = 0.0
    previous = 0.0
    for bracket in brackets:
        if bracket.up_to is None:
            slice_base = max(0.0, base - previous)
        else:
            slice_base = max(0.0, min(base, bracket.up_to) - previous)
            previous = bracket.up_to
        if slice_base <= 0:
            continue
        total += slice_base * bracket.rate
        if bracket.up_to is not None and base <= bracket.up_to:
            break
    return total


@dataclass(frozen=True)
class ScaleApplication:
    """Trazabilidad de una aplicación de escala progresiva."""

    scale_id: str
    base: float
    cuota: float
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class DeductionApplication:
    """Trazabilidad de una deducción aplicada a la cuota."""

    deduction_id: str
    amount: float
    bucket: str  # "estatal", "autonomica", "diferencial"


@dataclass(frozen=True)
class QuotaResult:
    devengo_date: date
    region: str
    tax_year: int

    base_imponible_general: float
    base_imponible_ahorro: float
    reducciones_aplicadas: float
    base_liquidable_general: float
    base_liquidable_ahorro: float

    minimo_personal_familiar: float
    minimo_aplicado_base_general: float
    minimo_aplicado_base_ahorro: float

    cuota_integra_estatal: float
    cuota_integra_autonomica: float | None
    cuota_integra_total: float | None

    deducciones_cuota_estatal: float
    deducciones_cuota_autonomica: float
    deducciones_cuota_diferencial: float

    cuota_liquida_estatal: float
    cuota_liquida_autonomica: float | None
    cuota_liquida_total: float | None

    retenciones_y_pagos_cuenta: float
    cuota_diferencial: float | None

    scale_applications: tuple[ScaleApplication, ...]
    deduction_applications: tuple[DeductionApplication, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)


def _sum_withholdings(profile: TaxProfile) -> float:
    total = 0.0
    for entry in profile.withholdings:
        if not isinstance(entry, dict):
            continue
        value = entry.get("amount")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value)
    return total


def _derive_base_imponible_general(profile: TaxProfile) -> float:
    income = profile.income
    if "base_imponible_general" in income:
        raw = income["base_imponible_general"]
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
    total = 0.0
    for key in (
        "work_net",
        "capital_inmobiliario_net",
        "economic_activity_net",
        "rental_housing_net",
        "imputaciones_renta",
    ):
        value = income.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value)
    return total


def _derive_base_imponible_ahorro(profile: TaxProfile) -> float:
    income = profile.income
    if "base_imponible_ahorro" in income:
        raw = income["base_imponible_ahorro"]
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return float(raw)
    total = 0.0
    for key in (
        "capital_mobiliario_net",
        "ganancias_patrimoniales_net",
    ):
        value = income.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += float(value)
    return total


def compute_quota(
    profile: TaxProfile,
    evaluations: list[RuleEvaluation],
    deductions_by_id: dict[str, Deduction],
    scales: list[TaxScale],
    devengo: date | None = None,
) -> QuotaResult:
    """Calcula la cuota IRPF completa a partir del perfil y las evaluaciones.

    `evaluations` debe ser la salida de `rules.evaluate_deductions(...)`. Solo
    se contabilizan las que tienen `status == "applies"`; el resto (incluidas
    `requires_manual_calculation`) se ignoran, lo que infraestima
    deliberadamente antes que inventar.
    """
    devengo = devengo or profile.effective_devengo_date()

    big = _derive_base_imponible_general(profile)
    bia = _derive_base_imponible_ahorro(profile)

    reducciones = 0.0
    mpf = 0.0
    deducciones_estatales_cuota = 0.0
    deducciones_autonomicas_cuota = 0.0
    deducciones_diferencial = 0.0
    deduction_applications: list[DeductionApplication] = []
    notes: list[str] = []

    for ev in evaluations:
        if ev.status != "applies":
            continue
        ded = deductions_by_id.get(ev.deduction_id)
        if ded is None:
            continue
        amount = float(ev.estimated_amount)
        if ded.category == DeductionCategory.REDUCCION:
            reducciones += amount
        elif ded.category == DeductionCategory.MINIMO_PERSONAL_FAMILIAR:
            mpf += amount
        elif ded.category == DeductionCategory.DEDUCCION:
            if _is_cuota_diferencial(ded):
                deducciones_diferencial += amount
                deduction_applications.append(
                    DeductionApplication(ev.deduction_id, amount, "diferencial")
                )
            elif ded.scope == Scope.ESTATAL:
                # Las deducciones estatales se reparten al 50% entre la cuota
                # estatal y la autonómica (LIRPF arts. 67 y 77).
                half = amount / 2.0
                deducciones_estatales_cuota += half
                deducciones_autonomicas_cuota += half
                deduction_applications.append(
                    DeductionApplication(ev.deduction_id, amount, "estatal")
                )
            elif ded.scope == Scope.AUTONOMICO:
                deducciones_autonomicas_cuota += amount
                deduction_applications.append(
                    DeductionApplication(ev.deduction_id, amount, "autonomica")
                )
            else:
                # FORAL/LOCAL: el motor no modela todavía el reparto; se
                # contabiliza como autonómica para no perderlas.
                deducciones_autonomicas_cuota += amount
                deduction_applications.append(
                    DeductionApplication(ev.deduction_id, amount, "autonomica")
                )
        # Otros (gasto_deducible, exencion, compensacion, ajuste) afectan a
        # las bases imponibles, no a la cuota: deben venir ya reflejados en
        # los netos del perfil.

    blg = max(0.0, big - reducciones)
    exceso_reducciones = max(0.0, reducciones - big)
    bla = max(0.0, bia - exceso_reducciones)

    mpf_aplicado_blg = min(mpf, blg)
    mpf_excedente = mpf - mpf_aplicado_blg

    estatal_general = select_scale(
        scales,
        tax_year=profile.tax_year,
        scope="estatal",
        component="general",
        region=None,
        devengo=devengo,
    )
    estatal_ahorro = select_scale(
        scales,
        tax_year=profile.tax_year,
        scope="estatal",
        component="ahorro",
        region=None,
        devengo=devengo,
    )
    autonomica_general = select_scale(
        scales,
        tax_year=profile.tax_year,
        scope="autonomico",
        component="general",
        region=profile.region,
        devengo=devengo,
    )

    scale_apps: list[ScaleApplication] = []

    if estatal_general is None:
        notes.append(
            f"No hay escala estatal general registrada para el ejercicio "
            f"{profile.tax_year}. La cuota íntegra estatal se devuelve a 0."
        )
        cuota_estatal_general = 0.0
    else:
        cuota_estatal_general = max(
            0.0,
            apply_progressive_scale(blg, estatal_general.brackets)
            - apply_progressive_scale(mpf_aplicado_blg, estatal_general.brackets),
        )
        scale_apps.append(
            ScaleApplication(
                estatal_general.id, blg, cuota_estatal_general, estatal_general.sources
            )
        )

    if estatal_ahorro is None:
        notes.append(
            f"No hay escala estatal del ahorro registrada para el ejercicio "
            f"{profile.tax_year}. La cuota del ahorro se devuelve a 0."
        )
        cuota_estatal_ahorro = 0.0
    else:
        cuota_estatal_ahorro = max(
            0.0,
            apply_progressive_scale(bla, estatal_ahorro.brackets)
            - apply_progressive_scale(mpf_excedente, estatal_ahorro.brackets),
        )
        scale_apps.append(
            ScaleApplication(
                estatal_ahorro.id, bla, cuota_estatal_ahorro, estatal_ahorro.sources
            )
        )

    cuota_integra_estatal = cuota_estatal_general + cuota_estatal_ahorro

    cuota_integra_autonomica: float | None
    if autonomica_general is None:
        cuota_integra_autonomica = None
        notes.append(
            f"No hay escala autonómica registrada para «{profile.region}» en "
            f"{profile.tax_year}. La cuota íntegra autonómica y, por tanto, "
            "la cuota líquida total y la cuota diferencial requieren "
            "cálculo manual hasta que se incorpore la escala correspondiente "
            "del boletín oficial autonómico."
        )
    else:
        cuota_auto_general = max(
            0.0,
            apply_progressive_scale(blg, autonomica_general.brackets)
            - apply_progressive_scale(mpf_aplicado_blg, autonomica_general.brackets),
        )
        # La parte autonómica del ahorro la fija el Estado (art. 76 LIRPF):
        # numéricamente coincide con la parte estatal del ahorro.
        cuota_auto_ahorro = cuota_estatal_ahorro
        cuota_integra_autonomica = cuota_auto_general + cuota_auto_ahorro
        scale_apps.append(
            ScaleApplication(
                autonomica_general.id,
                blg,
                cuota_auto_general,
                autonomica_general.sources,
            )
        )

    cuota_integra_total: float | None
    if cuota_integra_autonomica is None:
        cuota_integra_total = None
    else:
        cuota_integra_total = cuota_integra_estatal + cuota_integra_autonomica

    cuota_liquida_estatal = max(
        0.0, cuota_integra_estatal - deducciones_estatales_cuota
    )
    cuota_liquida_autonomica: float | None
    if cuota_integra_autonomica is None:
        cuota_liquida_autonomica = None
    else:
        cuota_liquida_autonomica = max(
            0.0, cuota_integra_autonomica - deducciones_autonomicas_cuota
        )

    cuota_liquida_total: float | None
    if cuota_liquida_autonomica is None:
        cuota_liquida_total = None
    else:
        cuota_liquida_total = cuota_liquida_estatal + cuota_liquida_autonomica

    retenciones = _sum_withholdings(profile)
    cuota_diferencial: float | None
    if cuota_liquida_total is None:
        cuota_diferencial = None
    else:
        cuota_diferencial = (
            cuota_liquida_total - retenciones - deducciones_diferencial
        )

    return QuotaResult(
        devengo_date=devengo,
        region=profile.region,
        tax_year=profile.tax_year,
        base_imponible_general=round(big, 2),
        base_imponible_ahorro=round(bia, 2),
        reducciones_aplicadas=round(reducciones, 2),
        base_liquidable_general=round(blg, 2),
        base_liquidable_ahorro=round(bla, 2),
        minimo_personal_familiar=round(mpf, 2),
        minimo_aplicado_base_general=round(mpf_aplicado_blg, 2),
        minimo_aplicado_base_ahorro=round(mpf_excedente, 2),
        cuota_integra_estatal=round(cuota_integra_estatal, 2),
        cuota_integra_autonomica=(
            None if cuota_integra_autonomica is None else round(cuota_integra_autonomica, 2)
        ),
        cuota_integra_total=(
            None if cuota_integra_total is None else round(cuota_integra_total, 2)
        ),
        deducciones_cuota_estatal=round(deducciones_estatales_cuota, 2),
        deducciones_cuota_autonomica=round(deducciones_autonomicas_cuota, 2),
        deducciones_cuota_diferencial=round(deducciones_diferencial, 2),
        cuota_liquida_estatal=round(cuota_liquida_estatal, 2),
        cuota_liquida_autonomica=(
            None if cuota_liquida_autonomica is None else round(cuota_liquida_autonomica, 2)
        ),
        cuota_liquida_total=(
            None if cuota_liquida_total is None else round(cuota_liquida_total, 2)
        ),
        retenciones_y_pagos_cuenta=round(retenciones, 2),
        cuota_diferencial=(
            None if cuota_diferencial is None else round(cuota_diferencial, 2)
        ),
        scale_applications=tuple(scale_apps),
        deduction_applications=tuple(deduction_applications),
        notes=tuple(notes),
    )


def quota_to_dict(quota: QuotaResult) -> dict[str, Any]:
    """Serializa un `QuotaResult` a dict JSON-friendly para la API y la BD."""
    return {
        "devengo_date": quota.devengo_date.isoformat(),
        "region": quota.region,
        "tax_year": quota.tax_year,
        "base_imponible_general": quota.base_imponible_general,
        "base_imponible_ahorro": quota.base_imponible_ahorro,
        "reducciones_aplicadas": quota.reducciones_aplicadas,
        "base_liquidable_general": quota.base_liquidable_general,
        "base_liquidable_ahorro": quota.base_liquidable_ahorro,
        "minimo_personal_familiar": quota.minimo_personal_familiar,
        "minimo_aplicado_base_general": quota.minimo_aplicado_base_general,
        "minimo_aplicado_base_ahorro": quota.minimo_aplicado_base_ahorro,
        "cuota_integra_estatal": quota.cuota_integra_estatal,
        "cuota_integra_autonomica": quota.cuota_integra_autonomica,
        "cuota_integra_total": quota.cuota_integra_total,
        "deducciones_cuota_estatal": quota.deducciones_cuota_estatal,
        "deducciones_cuota_autonomica": quota.deducciones_cuota_autonomica,
        "deducciones_cuota_diferencial": quota.deducciones_cuota_diferencial,
        "cuota_liquida_estatal": quota.cuota_liquida_estatal,
        "cuota_liquida_autonomica": quota.cuota_liquida_autonomica,
        "cuota_liquida_total": quota.cuota_liquida_total,
        "retenciones_y_pagos_cuenta": quota.retenciones_y_pagos_cuenta,
        "cuota_diferencial": quota.cuota_diferencial,
        "scale_applications": [
            {
                "scale_id": app.scale_id,
                "base": app.base,
                "cuota": app.cuota,
                "sources": [_source_to_dict(s) for s in app.sources],
            }
            for app in quota.scale_applications
        ],
        "deduction_applications": [
            {
                "deduction_id": app.deduction_id,
                "amount": app.amount,
                "bucket": app.bucket,
            }
            for app in quota.deduction_applications
        ],
        "notes": list(quota.notes),
    }


def _source_to_dict(source: Source) -> dict[str, Any]:
    return {
        "kind": source.kind.value,
        "title": source.title,
        "url": source.url,
        "article": source.article,
        "paragraph": source.paragraph,
        "boe_id": source.boe_id,
        "content_hash": source.content_hash,
        "checked_at": source.checked_at.isoformat() if source.checked_at else None,
    }
