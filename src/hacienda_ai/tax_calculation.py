"""Cálculo de la cuota IRPF a partir del perfil + reglas evaluadas.

Implementa el flujo de la declaración del IRPF al nivel necesario para que
el output del motor pase de "suma de importes deducibles" a "cuota líquida
diferencial real" en euros, que es lo que un asesor o un contribuyente
quiere ver.

Limitaciones intencionales del MVP
----------------------------------
- **Tarifa autonómica = tarifa estatal por defecto**: el tramo autonómico
  de la tarifa progresiva varía por CCAA. Para la mayoría de las CCAA del
  régimen común la suma estatal + autonómica se aproxima a los porcentajes
  agregados que usamos aquí (19/24/30/37/45/47). El módulo deja sitio
  para overrides cuando se modelen las CCAA reales.
- **Base imponible general y del ahorro son inputs**: el contribuyente o
  el wizard las calculan a partir de los rendimientos íntegros y los
  gastos deducibles. El motor NO recalcula las bases — sólo aplica las
  reducciones que correspondan tras las reglas del corpus.
- **Categorías `gasto_deducible` se asumen pre-descontadas**: las cuotas
  sindicales o colegiales del corpus reducen `income.work_income` antes
  de llegar a `taxable_base.general`. Si el wizard pasa el íntegro, el
  motor no las restará dos veces.
- Tarifa, mínimos y porcentajes son los del ejercicio 2025 (LIRPF tras
  Ley 22/2021 PGE 2022 y ajustes posteriores). Para cambiar de año habrá
  que parametrizar por `tax_year`.

Referencias normativas (texto consolidado LIRPF 2025)
-----------------------------------------------------
- Art. 56.2 LIRPF: doble escala del mínimo personal y familiar.
- Art. 57-61 LIRPF: cuantías de mínimos personales y familiares.
- Art. 63 LIRPF: tarifa general estatal.
- Art. 66 LIRPF: tarifa del ahorro.
- Art. 68 LIRPF: deducciones de la cuota.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .logging_setup import get_logger
from .models import Deduction, DeductionCategory, RuleEvaluation, TaxProfile

_logger = get_logger("tax")


# ---------- Tarifas IRPF 2025 (totales: estatal + autonómica genérica) ----------


@dataclass(frozen=True)
class TaxBracket:
    """Tramo de una tarifa progresiva. `up_to` None ⇒ tramo final sin tope."""

    up_to: float | None
    rate: float


@dataclass(frozen=True)
class TaxScale:
    name: str
    brackets: tuple[TaxBracket, ...]


# Tarifas IRPF 2025: la cuota total es la suma de la parte estatal y la parte
# autonómica, ambas progresivas. Cuando la CCAA no tiene una tarifa específica
# en este módulo, se aplica una "autonómica genérica" idéntica a la estatal,
# que produce la suma agregada usada por la mayoría de CCAAs del régimen común.

# Parte estatal de la tarifa general (art. 63 LIRPF, redacción consolidada
# tras Ley 22/2021).
STATE_GENERAL_TARIFF_2025: TaxScale = TaxScale(
    name="irpf_general_estatal_2025",
    brackets=(
        TaxBracket(up_to=12_450.0, rate=0.095),
        TaxBracket(up_to=20_200.0, rate=0.12),
        TaxBracket(up_to=35_200.0, rate=0.15),
        TaxBracket(up_to=60_000.0, rate=0.185),
        TaxBracket(up_to=300_000.0, rate=0.225),
        TaxBracket(up_to=None, rate=0.245),
    ),
)

# Autonómica genérica: réplica de la estatal. Usar este valor cuando la CCAA
# no tiene una tarifa específica registrada en AUTONOMIC_GENERAL_TARIFFS.
GENERIC_AUTONOMIC_GENERAL_TARIFF_2025: TaxScale = TaxScale(
    name="irpf_general_autonomica_generica_2025",
    brackets=STATE_GENERAL_TARIFF_2025.brackets,
)

# Tarifa total = 2x estatal (estatal + autonómica genérica idéntica).
# Esto es estrictamente coherente con la simplificación "autonómica = estatal":
# el tipo marginal máximo bajo este modelo es 49 % (2x 24,5 %), no 47 %. En
# la realidad, las CCAAs del régimen común tienen tarifas autonómicas
# ligeramente distintas (la "tarifa subsidiaria" del art. 65 LIRPF da
# aproximadamente 47 % en el tope), pero registrar esas cifras requiere
# verificación CCAA por CCAA. Hasta entonces, este agregado es la referencia.
GENERAL_TARIFF_2025: TaxScale = TaxScale(
    name="irpf_general_total_generic_2025",
    brackets=(
        TaxBracket(up_to=12_450.0, rate=0.19),
        TaxBracket(up_to=20_200.0, rate=0.24),
        TaxBracket(up_to=35_200.0, rate=0.30),
        TaxBracket(up_to=60_000.0, rate=0.37),
        TaxBracket(up_to=300_000.0, rate=0.45),
        TaxBracket(up_to=None, rate=0.49),
    ),
)

# Tarifa del ahorro (art. 66 LIRPF). La parte estatal y la autonómica son
# simétricas por ley, así que su suma es la tarifa total del ahorro.
STATE_SAVINGS_TARIFF_2025: TaxScale = TaxScale(
    name="irpf_ahorro_estatal_2025",
    brackets=(
        TaxBracket(up_to=6_000.0, rate=0.095),
        TaxBracket(up_to=50_000.0, rate=0.105),
        TaxBracket(up_to=200_000.0, rate=0.115),
        TaxBracket(up_to=300_000.0, rate=0.135),
        TaxBracket(up_to=None, rate=0.15),
    ),
)
AUTONOMIC_SAVINGS_TARIFF_2025: TaxScale = TaxScale(
    name="irpf_ahorro_autonomica_2025",
    brackets=STATE_SAVINGS_TARIFF_2025.brackets,
)
SAVINGS_TARIFF_2025: TaxScale = TaxScale(
    name="irpf_ahorro_total_2025",
    brackets=(
        TaxBracket(up_to=6_000.0, rate=0.19),
        TaxBracket(up_to=50_000.0, rate=0.21),
        TaxBracket(up_to=200_000.0, rate=0.23),
        TaxBracket(up_to=300_000.0, rate=0.27),
        TaxBracket(up_to=None, rate=0.30),
    ),
)


@dataclass(frozen=True)
class AutonomicTariffSet:
    """Tarifas autonómicas de una CCAA concreta. La del ahorro es simétrica
    con la estatal por ley, así que sólo la general puede divergir."""

    general: TaxScale


# Registry de tarifas autonómicas reales por CCAA. **Vacío al inicio** por
# honestidad: añadir cifras de cada CCAA requiere contraste contra su
# boletín autonómico vigente para el ejercicio. Cuando una CCAA no está en
# este registry, se aplica la tarifa autonómica genérica (idéntica a la
# estatal), de modo que la suma total coincide con `GENERAL_TARIFF_2025`.
#
# Cómo añadir una CCAA real:
#   1. Localizar la norma autonómica que aprueba la tarifa (Decreto Legislativo
#      o Ley autonómica de medidas fiscales) en el boletín oficial.
#   2. Crear una entrada en este dict con la TaxScale autonómica de esa CCAA
#      para el ejercicio.
#   3. Añadir un test en tests/test_tax_calculation.py que verifique las
#      cifras esperadas para esa CCAA frente a la genérica.
AUTONOMIC_GENERAL_TARIFFS: dict[str, AutonomicTariffSet] = {}


def autonomic_general_tariff_for(region: str | None) -> TaxScale:
    """Devuelve la tarifa autonómica general aplicable a la región del
    perfil. Cuando no hay tarifa específica registrada, se devuelve la
    genérica (idéntica a la estatal)."""
    if region is None:
        return GENERIC_AUTONOMIC_GENERAL_TARIFF_2025
    key = region.strip().lower()
    for known_key, tariff_set in AUTONOMIC_GENERAL_TARIFFS.items():
        if known_key.lower() == key:
            return tariff_set.general
    return GENERIC_AUTONOMIC_GENERAL_TARIFF_2025


# ---------- Mínimos personales y familiares 2025 (art. 57-61 LIRPF) ----------


PERSONAL_MINIMUM_BASE = 5_550.0
PERSONAL_MINIMUM_AGE_65_BONUS = 1_150.0
PERSONAL_MINIMUM_AGE_75_BONUS = 1_400.0

CHILD_MINIMUMS: tuple[float, ...] = (2_400.0, 2_700.0, 4_000.0, 4_500.0)
CHILD_UNDER_3_BONUS = 2_800.0

ASCENDANT_BASE = 1_150.0
ASCENDANT_75_BONUS = 1_400.0

DISABILITY_33_MINIMUM = 3_000.0
DISABILITY_65_MINIMUM = 9_000.0
DISABILITY_ASSISTANCE_BONUS = 3_000.0


# ---------- Funciones públicas ----------


def apply_scale(amount: float, scale: TaxScale) -> float:
    """Aplica la tarifa progresiva al `amount` y devuelve la cuota."""
    if amount <= 0:
        return 0.0
    tax = 0.0
    floor = 0.0
    for bracket in scale.brackets:
        chunk = (amount - floor) if bracket.up_to is None else min(amount, bracket.up_to) - floor
        if chunk <= 0:
            break
        tax += chunk * bracket.rate
        if bracket.up_to is None or amount <= bracket.up_to:
            break
        floor = bracket.up_to
    return tax


def compute_personal_family_minimum(profile: TaxProfile) -> float:
    """Calcula el mínimo personal y familiar (art. 57-61 LIRPF) a partir
    de la composición del perfil. Suma:

    - 5.550 € base + bonificaciones por edad del contribuyente.
    - Mínimo por descendientes (escalado por orden + bonus < 3 años).
    - Mínimo por ascendientes cualificantes (> 65 o discapacitados).
    - Mínimo por discapacidad del contribuyente.

    Discapacidad de descendientes/ascendientes se modela aparte (pendiente
    de PR específico). El wizard puede sobrescribir el resultado con
    `family.personal_family_minimum_override`.
    """
    override = profile.family.get("personal_family_minimum_override")
    if isinstance(override, (int, float)) and not isinstance(override, bool):
        return float(override)

    minimum = PERSONAL_MINIMUM_BASE
    personal = profile.personal
    family = profile.family

    age = _as_int(personal.get("age"))
    if age is not None:
        if age >= 65:
            minimum += PERSONAL_MINIMUM_AGE_65_BONUS
        if age >= 75:
            minimum += PERSONAL_MINIMUM_AGE_75_BONUS

    disability = _as_int(personal.get("disability_percentage"))
    if disability is not None:
        if disability >= 65:
            minimum += DISABILITY_65_MINIMUM
        elif disability >= 33:
            minimum += DISABILITY_33_MINIMUM
        if personal.get("needs_third_person_help") is True:
            minimum += DISABILITY_ASSISTANCE_BONUS

    children = _as_int(family.get("children_count")) or 0
    for index in range(1, children + 1):
        bracket = min(index, len(CHILD_MINIMUMS)) - 1
        minimum += CHILD_MINIMUMS[bracket]
    children_under_3 = _as_int(family.get("children_under_3_count")) or 0
    minimum += children_under_3 * CHILD_UNDER_3_BONUS

    ascendants = _as_int(family.get("ascendants_qualifying_count")) or 0
    minimum += ascendants * ASCENDANT_BASE
    ascendants_over_75 = _as_int(family.get("ascendants_over_75_count")) or 0
    minimum += ascendants_over_75 * ASCENDANT_75_BONUS

    return minimum


@dataclass(frozen=True)
class TaxSummary:
    """Desglose completo del cálculo IRPF aplicable al perfil."""

    tax_year: int
    region: str
    base_imponible_general: float
    base_imponible_ahorro: float
    reducciones_aplicadas: float
    base_liquidable_general: float
    base_liquidable_ahorro: float
    minimum_personal_y_familiar: float
    cuota_integra_general: float
    cuota_integra_ahorro: float
    cuota_correspondiente_al_minimo: float
    cuota_integra_total: float
    deducciones_de_cuota: float
    bonificaciones_cuota: float
    cuota_liquida: float
    retenciones: float
    cuota_diferencial: float
    applied_reduction_ids: tuple[str, ...]
    applied_cuota_deduction_ids: tuple[str, ...]
    applied_bonification_ids: tuple[str, ...]


def compute_tax_summary(
    profile: TaxProfile,
    deductions: list[Deduction],
    evaluations: list[RuleEvaluation],
) -> TaxSummary:
    """Calcula la cuota líquida del IRPF aplicando las evaluaciones que han
    quedado en estado `applies`. Las que están en `missing_evidence` quedan
    fuera (su importe no se aplica hasta que se aporten los justificantes).
    """
    deductions_by_id = {deduction.id: deduction for deduction in deductions}

    base_general = _profile_number(profile.taxable_base.get("general"))
    base_savings = _profile_number(profile.taxable_base.get("savings"))
    minimum = compute_personal_family_minimum(profile)

    reducciones_total = 0.0
    applied_reduction_ids: list[str] = []
    deducciones_cuota_total = 0.0
    applied_cuota_deduction_ids: list[str] = []
    bonificaciones_total = 0.0
    applied_bonification_ids: list[str] = []

    for evaluation in evaluations:
        if evaluation.status != "applies":
            continue
        deduction = deductions_by_id.get(evaluation.deduction_id)
        if deduction is None:
            continue
        if deduction.category == DeductionCategory.REDUCCION:
            reducciones_total += evaluation.estimated_amount
            applied_reduction_ids.append(deduction.id)
        elif deduction.category == DeductionCategory.DEDUCCION:
            if deduction.calculation.type == "cuota_bonification":
                bonificaciones_total += evaluation.estimated_amount
                applied_bonification_ids.append(deduction.id)
            else:
                deducciones_cuota_total += evaluation.estimated_amount
                applied_cuota_deduction_ids.append(deduction.id)
        # gasto_deducible, exencion, compensacion, ajuste, minimo_personal_familiar:
        # se ignoran en este cálculo (asumimos pre-descontados o fuera de scope).

    base_liquidable_general = max(0.0, base_general - reducciones_total)
    base_liquidable_ahorro = max(0.0, base_savings)

    # Doble escala del mínimo personal y familiar (art. 56.2 LIRPF).
    absorbed_by_general = min(minimum, base_liquidable_general)
    minimum_remainder = max(0.0, minimum - absorbed_by_general)
    absorbed_by_savings = min(minimum_remainder, base_liquidable_ahorro)

    # Tarifas: estatal + autonómica (la autonómica depende de la CCAA).
    autonomic_general = autonomic_general_tariff_for(profile.region)

    state_general_full = apply_scale(base_liquidable_general, STATE_GENERAL_TARIFF_2025)
    state_general_minimum = apply_scale(absorbed_by_general, STATE_GENERAL_TARIFF_2025)
    state_general_net = max(0.0, state_general_full - state_general_minimum)

    autonomic_general_full = apply_scale(base_liquidable_general, autonomic_general)
    autonomic_general_minimum = apply_scale(absorbed_by_general, autonomic_general)
    autonomic_general_net = max(0.0, autonomic_general_full - autonomic_general_minimum)

    state_savings_full = apply_scale(base_liquidable_ahorro, STATE_SAVINGS_TARIFF_2025)
    state_savings_minimum = apply_scale(absorbed_by_savings, STATE_SAVINGS_TARIFF_2025)
    state_savings_net = max(0.0, state_savings_full - state_savings_minimum)

    autonomic_savings_full = apply_scale(base_liquidable_ahorro, AUTONOMIC_SAVINGS_TARIFF_2025)
    autonomic_savings_minimum = apply_scale(absorbed_by_savings, AUTONOMIC_SAVINGS_TARIFF_2025)
    autonomic_savings_net = max(0.0, autonomic_savings_full - autonomic_savings_minimum)

    cuota_integra_general = state_general_net + autonomic_general_net
    cuota_integra_ahorro = state_savings_net + autonomic_savings_net
    cuota_integra_total = cuota_integra_general + cuota_integra_ahorro
    cuota_correspondiente_al_minimo = (
        state_general_minimum + autonomic_general_minimum + state_savings_minimum + autonomic_savings_minimum
    )

    cuota_liquida = max(0.0, cuota_integra_total - deducciones_cuota_total - bonificaciones_total)

    retenciones = _withholdings_total(profile)
    cuota_diferencial = cuota_liquida - retenciones

    summary = TaxSummary(
        tax_year=profile.tax_year,
        region=profile.region,
        base_imponible_general=base_general,
        base_imponible_ahorro=base_savings,
        reducciones_aplicadas=reducciones_total,
        base_liquidable_general=base_liquidable_general,
        base_liquidable_ahorro=base_liquidable_ahorro,
        minimum_personal_y_familiar=minimum,
        cuota_integra_general=cuota_integra_general,
        cuota_integra_ahorro=cuota_integra_ahorro,
        cuota_correspondiente_al_minimo=cuota_correspondiente_al_minimo,
        cuota_integra_total=cuota_integra_total,
        deducciones_de_cuota=deducciones_cuota_total,
        bonificaciones_cuota=bonificaciones_total,
        cuota_liquida=cuota_liquida,
        retenciones=retenciones,
        cuota_diferencial=cuota_diferencial,
        applied_reduction_ids=tuple(applied_reduction_ids),
        applied_cuota_deduction_ids=tuple(applied_cuota_deduction_ids),
        applied_bonification_ids=tuple(applied_bonification_ids),
    )
    _logger.info(
        "tax_summary_computed",
        extra={
            "tax_year": profile.tax_year,
            "reductions_count": len(applied_reduction_ids),
            "cuota_deductions_count": len(applied_cuota_deduction_ids),
            "bonifications_count": len(applied_bonification_ids),
            # Sin importes en logs (ver docs/rgpd-logging.md).
        },
    )
    return summary


@dataclass(frozen=True)
class RuleSaving:
    """Ahorro marginal atribuible a una regla concreta: cuánto subiría la
    cuota diferencial si esa regla NO se aplicara, dejando el resto igual.

    Nota: la suma de los `ahorro_marginal` no equivale en general al
    `ahorro_real` del agregado por la no-linealidad de la tarifa y los
    topes interrelacionados (`taxable_base_limits`, incompatibilidades).
    """

    deduction_id: str
    ahorro_marginal: float


@dataclass(frozen=True)
class TaxComparison:
    """Compara cuota IRPF con todas las reglas aplicadas vs. una baseline
    sin ninguna regla aplicada. El `ahorro_real` es la diferencia exacta
    entre cuotas diferenciales — NO la suma de importes de las reglas,
    que sobreestima por tratar reducción de base y deducción de cuota
    como equivalentes."""

    with_rules: TaxSummary
    without_rules: TaxSummary
    ahorro_real: float
    savings_per_rule: tuple[RuleSaving, ...]


def compute_tax_comparison(
    profile: TaxProfile,
    deductions: list[Deduction],
    evaluations: list[RuleEvaluation],
) -> TaxComparison:
    """Compara la cuota con/sin reglas y devuelve el ahorro real más el
    detalle marginal por regla."""
    with_rules = compute_tax_summary(profile, deductions, evaluations)
    baseline_evaluations = tuple(_disable(evaluation) for evaluation in evaluations)
    without_rules = compute_tax_summary(profile, deductions, list(baseline_evaluations))
    ahorro_real = without_rules.cuota_diferencial - with_rules.cuota_diferencial

    savings_per_rule = _per_rule_savings(profile, deductions, evaluations, with_rules.cuota_diferencial)
    _logger.info(
        "tax_comparison_computed",
        extra={
            "tax_year": profile.tax_year,
            "rules_compared": len(savings_per_rule),
            "has_savings": ahorro_real > 0,
        },
    )
    return TaxComparison(
        with_rules=with_rules,
        without_rules=without_rules,
        ahorro_real=ahorro_real,
        savings_per_rule=savings_per_rule,
    )


def _per_rule_savings(
    profile: TaxProfile,
    deductions: list[Deduction],
    evaluations: list[RuleEvaluation],
    cuota_diferencial_with_all: float,
) -> tuple[RuleSaving, ...]:
    """Para cada regla en estado `applies`, calcula cuánto subiría la
    cuota diferencial si ESA regla se quitara (el resto se mantiene)."""
    applying_ids = [evaluation.deduction_id for evaluation in evaluations if evaluation.status == "applies"]
    savings: list[RuleSaving] = []
    for rule_id in applying_ids:
        modified = [
            _disable(evaluation) if evaluation.deduction_id == rule_id else evaluation for evaluation in evaluations
        ]
        partial = compute_tax_summary(profile, deductions, modified)
        marginal = partial.cuota_diferencial - cuota_diferencial_with_all
        savings.append(RuleSaving(deduction_id=rule_id, ahorro_marginal=marginal))
    return tuple(savings)


def _disable(evaluation: RuleEvaluation) -> RuleEvaluation:
    return replace(evaluation, status="does_not_apply", estimated_amount=0.0)


# ---------- helpers internos ----------


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _profile_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _withholdings_total(profile: TaxProfile) -> float:
    total = 0.0
    for entry in profile.withholdings:
        if isinstance(entry, dict):
            amount = entry.get("amount")
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                total += float(amount)
    return total
