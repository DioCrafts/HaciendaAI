"""Calendario fiscal español y resolución temporal del ejercicio.

Provee dos capacidades complementarias a la conciencia temporal del
asistente:

1. **`resolve_current_fiscal_year(today)`**: dada una fecha actual,
   devuelve el ejercicio fiscal en curso, el último totalmente
   devengado, el último cuya campaña de Renta ya está abierta o
   acaba de cerrar, y una recomendación heurística para preguntas
   genéricas ("¿cuánto pago de IRPF?" sin más contexto). El LLM
   debe llamar a esta función antes de asumir un año.

2. **`get_upcoming_events(today, window_days, segments)`**: lista
   las próximas obligaciones fiscales (modelos AEAT) dentro de una
   ventana temporal, filtrables por segmento de contribuyente
   (particular, autónomo, sociedad). Cada obligación incluye
   modelo, periodo cubierto, fecha límite (ajustada al primer día
   hábil si cae en fin de semana), descripción y normativa.

El catálogo de obligaciones está en código y cubre los modelos clave
del calendario AEAT. Las fechas siguen las reglas estables del
Reglamento General de Gestión e Inspección y las publicaciones
anuales de AEAT. NO contempla festivos nacionales/autonómicos en el
ajuste a día hábil — para plazos críticos, el operador debe contrastar
con el calendario oficial de la AEAT (sede.agenciatributaria.gob.es).

Diseño:

- Todas las funciones aceptan `today` opcional y por defecto usan
  `date.today()`. Inyectable para tests deterministas.
- Las dataclasses son `frozen=True` para evitar mutaciones accidentales
  en código de chat (los handlers de tools pueden serializar a dict
  sin riesgo de modificación).
- Sin dependencias externas: solo `datetime` + stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import Any, Iterable

# Ventanas estables del calendario fiscal AEAT.
_RENTA_OPEN_MONTH = 4  # 1 de abril (aprox; algunos años empieza el 2 o 3)
_RENTA_OPEN_DAY = 1
_RENTA_CLOSE_MONTH = 6  # 30 de junio
_RENTA_CLOSE_DAY = 30


class Periodicity(str, Enum):
    """Frecuencia de presentación de una obligación tributaria."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class TaxpayerSegment(str, Enum):
    """Segmento de contribuyente al que aplica una obligación.

    Usado para filtrar el calendario: un particular sin actividad no
    presenta 303 ni 111; una S.L. típica presenta 200, 202, 111, 303,
    347, 349. Foral (Bizkaia/Gipuzkoa/Alava/Navarra) tiene calendario
    propio que NO contemplamos aquí.
    """

    PARTICULAR = "particular"
    AUTONOMO = "autonomo"
    AUTONOMO_MODULOS = "autonomo_modulos"
    EMPRESA = "empresa"
    GRAN_EMPRESA = "gran_empresa"  # facturación > 6M €, IVA mensual


class DeadlineSchedule(str, Enum):
    """Patrón canónico de fechas de presentación.

    Cada patrón se materializa en fechas concretas para un año dado
    vía `_compute_deadlines(schedule, year)`. Mantener los patrones
    como enum (y no como lambdas en cada `FiscalEvent`) los hace
    auditables y permite testear cada uno en aislamiento.
    """

    QUARTERLY_T_20 = "quarterly_t_20"  # 1T→20-abr, 2T→20-jul, 3T→20-oct, 4T→30-ene+1
    QUARTERLY_T_20_4T_30_JAN = "quarterly_t_20_4t_30_jan"  # variante 4T → 30-ene del año siguiente
    ANNUAL_JAN_30 = "annual_jan_30"
    ANNUAL_FEB_28 = "annual_feb_28"
    ANNUAL_MAR_31 = "annual_mar_31"
    ANNUAL_RENTA_APR_JUN = "annual_renta_apr_jun"
    ANNUAL_IS_JUL_25 = "annual_is_jul_25"
    PAYMENT_IS_APR_OCT_DEC_20 = "payment_is_apr_oct_dec_20"


# ---------- FiscalEvent (catálogo) ----------


@dataclass(frozen=True)
class FiscalEvent:
    """Definición declarativa de una obligación fiscal periódica.

    `code` es el número de modelo AEAT (`100`, `303`, …). `impuesto`
    es la figura tributaria (`irpf`, `iva`, `is`, `retenciones`,
    `informativos`, `extranjero`). `applicable_to` filtra el evento
    por segmento; un evento puede aplicar a varios.
    """

    code: str
    name: str
    impuesto: str
    periodicity: Periodicity
    schedule: DeadlineSchedule
    applicable_to: tuple[TaxpayerSegment, ...]
    description: str
    normativa: tuple[str, ...] = field(default_factory=tuple)


CATALOG: tuple[FiscalEvent, ...] = (
    FiscalEvent(
        code="100",
        name="Declaración anual del IRPF",
        impuesto="irpf",
        periodicity=Periodicity.ANNUAL,
        schedule=DeadlineSchedule.ANNUAL_RENTA_APR_JUN,
        applicable_to=(TaxpayerSegment.PARTICULAR, TaxpayerSegment.AUTONOMO),
        description=(
            "Declaración anual del IRPF (Renta). Plazo aprox. del 1 de abril "
            "al 30 de junio del año siguiente al devengo. Para domiciliación "
            "bancaria, el plazo termina el 25 de junio."
        ),
        normativa=("Ley 35/2006 art. 96",),
    ),
    FiscalEvent(
        code="130",
        name="Pago fraccionado IRPF (estimación directa)",
        impuesto="irpf",
        periodicity=Periodicity.QUARTERLY,
        schedule=DeadlineSchedule.QUARTERLY_T_20_4T_30_JAN,
        applicable_to=(TaxpayerSegment.AUTONOMO,),
        description=(
            "Autoliquidación trimestral del pago a cuenta del IRPF para "
            "autónomos en estimación directa. 1T-3T hasta el día 20; 4T "
            "hasta el 30 de enero del año siguiente."
        ),
        normativa=("RD 439/2007 arts. 109-110",),
    ),
    FiscalEvent(
        code="131",
        name="Pago fraccionado IRPF (estimación objetiva)",
        impuesto="irpf",
        periodicity=Periodicity.QUARTERLY,
        schedule=DeadlineSchedule.QUARTERLY_T_20_4T_30_JAN,
        applicable_to=(TaxpayerSegment.AUTONOMO_MODULOS,),
        description=(
            "Autoliquidación trimestral del IRPF en módulos. Mismas fechas "
            "que el 130."
        ),
        normativa=("RD 439/2007 arts. 109-110",),
    ),
    FiscalEvent(
        code="303",
        name="Autoliquidación trimestral del IVA",
        impuesto="iva",
        periodicity=Periodicity.QUARTERLY,
        schedule=DeadlineSchedule.QUARTERLY_T_20_4T_30_JAN,
        applicable_to=(TaxpayerSegment.AUTONOMO, TaxpayerSegment.EMPRESA),
        description=(
            "Autoliquidación del IVA. Régimen general trimestral: 1T-3T "
            "hasta el día 20; 4T hasta el 30 de enero del año siguiente. "
            "Grandes empresas y régimen mensual: hasta el día 30 del mes "
            "siguiente."
        ),
        normativa=("Ley 37/1992 art. 167",),
    ),
    FiscalEvent(
        code="390",
        name="Resumen anual del IVA",
        impuesto="iva",
        periodicity=Periodicity.ANNUAL,
        schedule=DeadlineSchedule.ANNUAL_JAN_30,
        applicable_to=(TaxpayerSegment.AUTONOMO, TaxpayerSegment.EMPRESA),
        description=(
            "Declaración-resumen anual del IVA. Plazo: hasta el 30 de enero "
            "del año siguiente al ejercicio."
        ),
        normativa=("Ley 37/1992 art. 164.1.6º",),
    ),
    FiscalEvent(
        code="111",
        name="Retenciones e ingresos a cuenta (rendimientos del trabajo y profesionales)",
        impuesto="retenciones",
        periodicity=Periodicity.QUARTERLY,
        schedule=DeadlineSchedule.QUARTERLY_T_20,
        applicable_to=(TaxpayerSegment.AUTONOMO, TaxpayerSegment.EMPRESA),
        description=(
            "Autoliquidación de retenciones e ingresos a cuenta de "
            "rendimientos del trabajo y de actividades económicas. Día 20 "
            "del mes siguiente al cierre del periodo (mensual o trimestral)."
        ),
        normativa=("RD 439/2007 art. 108",),
    ),
    FiscalEvent(
        code="115",
        name="Retenciones por arrendamientos urbanos",
        impuesto="retenciones",
        periodicity=Periodicity.QUARTERLY,
        schedule=DeadlineSchedule.QUARTERLY_T_20,
        applicable_to=(TaxpayerSegment.AUTONOMO, TaxpayerSegment.EMPRESA),
        description=(
            "Autoliquidación de retenciones por arrendamientos de inmuebles "
            "urbanos. Mismas fechas que el 111."
        ),
        normativa=("RD 439/2007 art. 100",),
    ),
    FiscalEvent(
        code="190",
        name="Resumen anual de retenciones (trabajo, profesional, premios)",
        impuesto="retenciones",
        periodicity=Periodicity.ANNUAL,
        schedule=DeadlineSchedule.ANNUAL_JAN_30,
        applicable_to=(TaxpayerSegment.AUTONOMO, TaxpayerSegment.EMPRESA),
        description=(
            "Declaración-resumen anual de retenciones e ingresos a cuenta. "
            "Plazo: hasta el 30 de enero del año siguiente."
        ),
        normativa=("RD 1065/2007 art. 33",),
    ),
    FiscalEvent(
        code="202",
        name="Pago fraccionado IS",
        impuesto="is",
        periodicity=Periodicity.QUARTERLY,
        schedule=DeadlineSchedule.PAYMENT_IS_APR_OCT_DEC_20,
        applicable_to=(TaxpayerSegment.EMPRESA, TaxpayerSegment.GRAN_EMPRESA),
        description=(
            "Pago fraccionado del Impuesto sobre Sociedades. Tres plazos "
            "anuales: 20 de abril, 20 de octubre y 20 de diciembre."
        ),
        normativa=("Ley 27/2014 art. 40",),
    ),
    FiscalEvent(
        code="200",
        name="Declaración anual del Impuesto sobre Sociedades",
        impuesto="is",
        periodicity=Periodicity.ANNUAL,
        schedule=DeadlineSchedule.ANNUAL_IS_JUL_25,
        applicable_to=(TaxpayerSegment.EMPRESA, TaxpayerSegment.GRAN_EMPRESA),
        description=(
            "Declaración anual del IS. Plazo: 25 días posteriores a los "
            "6 meses tras el cierre del periodo impositivo; para ejercicios "
            "coincidentes con año natural, hasta el 25 de julio."
        ),
        normativa=("Ley 27/2014 art. 124",),
    ),
    FiscalEvent(
        code="347",
        name="Declaración informativa de operaciones con terceros",
        impuesto="informativos",
        periodicity=Periodicity.ANNUAL,
        schedule=DeadlineSchedule.ANNUAL_FEB_28,
        applicable_to=(TaxpayerSegment.AUTONOMO, TaxpayerSegment.EMPRESA),
        description=(
            "Operaciones con terceras personas (clientes/proveedores) por "
            "importe anual superior a 3.005,06 €. Plazo: hasta el 28 (o 29 "
            "en bisiestos) de febrero."
        ),
        normativa=("RD 1065/2007 arts. 31-35",),
    ),
    FiscalEvent(
        code="349",
        name="Operaciones intracomunitarias",
        impuesto="iva",
        periodicity=Periodicity.QUARTERLY,
        schedule=DeadlineSchedule.QUARTERLY_T_20_4T_30_JAN,
        applicable_to=(TaxpayerSegment.AUTONOMO, TaxpayerSegment.EMPRESA),
        description=(
            "Declaración recapitulativa de operaciones intracomunitarias. "
            "Periodicidad por defecto trimestral; mensual si las entregas "
            "intracomunitarias del trimestre o de alguno de los 4 trimestres "
            "previos superan 50.000 €."
        ),
        normativa=("Ley 37/1992 art. 164.1.5º",),
    ),
    FiscalEvent(
        code="720",
        name="Declaración informativa de bienes y derechos en el extranjero",
        impuesto="extranjero",
        periodicity=Periodicity.ANNUAL,
        schedule=DeadlineSchedule.ANNUAL_MAR_31,
        applicable_to=(TaxpayerSegment.PARTICULAR, TaxpayerSegment.AUTONOMO, TaxpayerSegment.EMPRESA),
        description=(
            "Obligación informativa de bienes y derechos situados en el "
            "extranjero (cuentas, valores, inmuebles) cuando alguno de los "
            "bloques supere 50.000 €. Plazo: hasta el 31 de marzo."
        ),
        normativa=("Disposición adicional 18ª LGT", "RD 1065/2007 arts. 42 bis-quater"),
    ),
    FiscalEvent(
        code="721",
        name="Declaración informativa de criptoactivos en el extranjero",
        impuesto="extranjero",
        periodicity=Periodicity.ANNUAL,
        schedule=DeadlineSchedule.ANNUAL_MAR_31,
        applicable_to=(TaxpayerSegment.PARTICULAR, TaxpayerSegment.AUTONOMO, TaxpayerSegment.EMPRESA),
        description=(
            "Información sobre criptoactivos situados en el extranjero "
            "(saldos a 31-dic) cuando superen 50.000 €. Plazo: hasta el "
            "31 de marzo."
        ),
        normativa=("Disposición adicional 18ª LGT", "RD 249/2023"),
    ),
)

CATALOG_BY_CODE: dict[str, FiscalEvent] = {e.code: e for e in CATALOG}


# ---------- Cálculo de fechas ----------


def _shift_to_next_business_day(d: date) -> date:
    """Si la fecha cae en sábado o domingo, mueve al lunes siguiente.

    NO contempla festivos nacionales ni autonómicos: para plazos
    críticos el operador debe consultar el calendario AEAT oficial.
    El sistema indica el cálculo aproximado; nunca debe usarse como
    fuente única para una presentación.
    """
    if d.weekday() == 5:  # sábado
        return d + timedelta(days=2)
    if d.weekday() == 6:  # domingo
        return d + timedelta(days=1)
    return d


def _compute_deadlines(
    schedule: DeadlineSchedule, year: int
) -> list[tuple[date, str]]:
    """Devuelve `[(deadline, period_label), ...]` para el año calendario `year`.

    `period_label` describe el periodo cubierto por la presentación
    (`"1T YYYY"`, `"Ejercicio YYYY"`, etc.) — útil para que el
    contribuyente entienda QUÉ está declarando, no solo cuándo.
    """
    if schedule == DeadlineSchedule.QUARTERLY_T_20:
        # 1T abr-20, 2T jul-20, 3T oct-20, 4T ene-20+1
        return [
            (_shift_to_next_business_day(date(year, 4, 20)), f"1T {year}"),
            (_shift_to_next_business_day(date(year, 7, 20)), f"2T {year}"),
            (_shift_to_next_business_day(date(year, 10, 20)), f"3T {year}"),
            (_shift_to_next_business_day(date(year + 1, 1, 20)), f"4T {year}"),
        ]
    if schedule == DeadlineSchedule.QUARTERLY_T_20_4T_30_JAN:
        # 1T abr-20, 2T jul-20, 3T oct-20, 4T ene-30+1 (regla 303/130/349)
        return [
            (_shift_to_next_business_day(date(year, 4, 20)), f"1T {year}"),
            (_shift_to_next_business_day(date(year, 7, 20)), f"2T {year}"),
            (_shift_to_next_business_day(date(year, 10, 20)), f"3T {year}"),
            (_shift_to_next_business_day(date(year + 1, 1, 30)), f"4T {year}"),
        ]
    if schedule == DeadlineSchedule.ANNUAL_JAN_30:
        # Resumen anual presentado el año siguiente.
        return [
            (
                _shift_to_next_business_day(date(year + 1, 1, 30)),
                f"Ejercicio {year}",
            )
        ]
    if schedule == DeadlineSchedule.ANNUAL_FEB_28:
        # 28-feb (29 en bisiestos del año siguiente).
        day = 29 if _is_leap(year + 1) else 28
        return [
            (
                _shift_to_next_business_day(date(year + 1, 2, day)),
                f"Ejercicio {year}",
            )
        ]
    if schedule == DeadlineSchedule.ANNUAL_MAR_31:
        return [
            (
                _shift_to_next_business_day(date(year + 1, 3, 31)),
                f"Ejercicio {year}",
            )
        ]
    if schedule == DeadlineSchedule.ANNUAL_RENTA_APR_JUN:
        # Renta: abre 1-abr año+1, cierra 30-jun año+1.
        return [
            (
                _shift_to_next_business_day(date(year + 1, 6, 30)),
                f"Ejercicio {year}",
            )
        ]
    if schedule == DeadlineSchedule.ANNUAL_IS_JUL_25:
        # IS: 25-jul año+1 (ejercicio coincidente con año natural).
        return [
            (
                _shift_to_next_business_day(date(year + 1, 7, 25)),
                f"Ejercicio {year}",
            )
        ]
    if schedule == DeadlineSchedule.PAYMENT_IS_APR_OCT_DEC_20:
        # 1P abr-20, 2P oct-20, 3P dic-20.
        return [
            (_shift_to_next_business_day(date(year, 4, 20)), f"1P {year}"),
            (_shift_to_next_business_day(date(year, 10, 20)), f"2P {year}"),
            (_shift_to_next_business_day(date(year, 12, 20)), f"3P {year}"),
        ]
    raise ValueError(f"DeadlineSchedule no soportado: {schedule!r}")


def _is_leap(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0


# ---------- Resolución del ejercicio fiscal ----------


@dataclass(frozen=True)
class RentaCampaignStatus:
    """Estado de la campaña de la Renta a una fecha dada.

    `tax_year` es el ejercicio que se declara en esa campaña
    (devengado el 31-dic del año anterior a la apertura). `opened_at`
    y `closed_at` son el 1-abr y el 30-jun del año de la campaña.
    """

    tax_year: int
    opened_at: date
    closed_at: date
    today: date

    @property
    def is_open(self) -> bool:
        return self.opened_at <= self.today <= self.closed_at

    @property
    def is_before_open(self) -> bool:
        return self.today < self.opened_at

    @property
    def is_after_close(self) -> bool:
        return self.today > self.closed_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "tax_year": self.tax_year,
            "opened_at": self.opened_at.isoformat(),
            "closed_at": self.closed_at.isoformat(),
            "is_open": self.is_open,
            "is_before_open": self.is_before_open,
            "is_after_close": self.is_after_close,
        }


@dataclass(frozen=True)
class FiscalYearResolution:
    """Resolución temporal contextualizada a una fecha de referencia.

    Distingue tres "años fiscales" útiles para el LLM:

    - `in_progress_year`: ejercicio en curso (= today.year). Es el
      año que el contribuyente está viviendo; aún no devengado salvo
      el 31-dic.
    - `last_closed_year`: último ejercicio totalmente devengado
      (= today.year - 1). El que el contribuyente debe declarar
      cuando se abra la campaña.
    - `last_declarable_year`: último ejercicio cuya campaña de
      presentación YA empezó. En enero-marzo es `today.year - 2`
      (campaña aún por abrir); a partir de abril es `today.year - 1`.

    `recommended_for_irpf_query` es la heurística cuando el usuario
    pregunta sobre IRPF sin especificar año: se asume el ejercicio
    declarable más reciente (más útil en el 90% de los casos).
    """

    today: date
    in_progress_year: int
    last_closed_year: int
    last_declarable_year: int
    renta_campaign: RentaCampaignStatus
    recommended_for_irpf_query: int

    @property
    def recommended_devengo(self) -> date:
        """31-dic del ejercicio recomendado — devengo IRPF estándar."""
        return date(self.recommended_for_irpf_query, 12, 31)

    def to_dict(self) -> dict[str, Any]:
        return {
            "today": self.today.isoformat(),
            "in_progress_year": self.in_progress_year,
            "last_closed_year": self.last_closed_year,
            "last_declarable_year": self.last_declarable_year,
            "renta_campaign": self.renta_campaign.to_dict(),
            "recommended_for_irpf_query": self.recommended_for_irpf_query,
            "recommended_devengo": self.recommended_devengo.isoformat(),
        }


def resolve_current_fiscal_year(today: date | None = None) -> FiscalYearResolution:
    """Calcula el contexto temporal fiscal para una fecha de referencia.

    Reglas (España, régimen común; foral no contemplado):

    - El ejercicio fiscal del IRPF coincide con el año natural; devengo
      31 de diciembre. `in_progress_year = today.year`,
      `last_closed_year = today.year - 1`.
    - La campaña de Renta del ejercicio N se abre el 1 de abril de
      N+1 y cierra el 30 de junio de N+1.
    - **`renta_campaign`** referencia la campaña del año natural en
      curso (`today.year`): abre el 1-abr-today.year, cierra el
      30-jun-today.year y declara el ejercicio `today.year - 1`. El
      estado (`is_before_open` / `is_open` / `is_after_close`) se
      deriva comparando `today` con esas fechas.
    - **`last_declarable_year`** distingue lo que ya se puede
      presentar formalmente:
      * Si `today < 1-abr`: la campaña aún no abrió; el último
        ejercicio declarable es `today.year - 2` (presentado en la
        campaña del año pasado).
      * Si `today >= 1-abr`: la campaña del ejercicio `today.year - 1`
        está activa o ya cerrada; ese es el último declarable.
    - **`recommended_for_irpf_query`** = `last_closed_year`
      (`today.year - 1`). Es el ejercicio sobre el que casi siempre se
      pregunta: "mi Renta" para alguien en enero significa la que aún
      no ha presentado (la del año recién terminado), no la de hace
      dos años. Si el usuario aclara otro año, el LLM lo respeta —
      esto es solo el default razonable.
    """
    if today is None:
        today = date.today()

    in_progress = today.year
    last_closed = today.year - 1

    open_day = date(today.year, _RENTA_OPEN_MONTH, _RENTA_OPEN_DAY)
    if today < open_day:
        last_declarable = today.year - 2
    else:
        last_declarable = today.year - 1

    renta = RentaCampaignStatus(
        # Campaña del año en curso → declara el ejercicio del año
        # anterior (devengo 31-dic). Esto es estable: la propiedad
        # `is_before_open` deja claro si todavía no se puede declarar.
        tax_year=today.year - 1,
        opened_at=open_day,
        closed_at=date(today.year, _RENTA_CLOSE_MONTH, _RENTA_CLOSE_DAY),
        today=today,
    )

    return FiscalYearResolution(
        today=today,
        in_progress_year=in_progress,
        last_closed_year=last_closed,
        last_declarable_year=last_declarable,
        renta_campaign=renta,
        recommended_for_irpf_query=last_closed,
    )


# ---------- Próximas obligaciones ----------


@dataclass(frozen=True)
class UpcomingEvent:
    """Obligación fiscal cuya fecha límite está dentro de la ventana."""

    code: str
    name: str
    impuesto: str
    period_label: str
    deadline: date
    days_until: int
    description: str
    applicable_to: tuple[str, ...]
    normativa: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "impuesto": self.impuesto,
            "period_label": self.period_label,
            "deadline": self.deadline.isoformat(),
            "days_until": self.days_until,
            "description": self.description,
            "applicable_to": list(self.applicable_to),
            "normativa": list(self.normativa),
        }


def get_upcoming_events(
    today: date | None = None,
    *,
    window_days: int = 90,
    segments: Iterable[TaxpayerSegment] | None = None,
    catalog: tuple[FiscalEvent, ...] = CATALOG,
) -> list[UpcomingEvent]:
    """Lista las obligaciones cuya fecha límite cae en `[today, today+window_days]`.

    `segments` filtra por aplicabilidad: si no se pasa, se incluyen
    todos los eventos (vista panorámica). El resultado se ordena por
    fecha (la más próxima primero), con desempate por `code`.

    Iteramos los años candidatos (el del `today` y los ±2 vecinos) y
    descartamos los deadlines fuera de la ventana — más limpio que
    intentar precalcular qué año aporta qué fechas.
    """
    if today is None:
        today = date.today()
    if window_days < 0:
        raise ValueError("window_days debe ser >= 0")

    segment_filter: set[TaxpayerSegment] | None
    if segments is None:
        segment_filter = None
    else:
        segment_filter = set(segments)
        if not segment_filter:
            return []

    end = today + timedelta(days=window_days)
    results: list[UpcomingEvent] = []
    candidate_years = (today.year - 2, today.year - 1, today.year, today.year + 1)

    for event in catalog:
        if segment_filter is not None and not (
            segment_filter & set(event.applicable_to)
        ):
            continue
        for year in candidate_years:
            for deadline, period_label in _compute_deadlines(event.schedule, year):
                if deadline < today or deadline > end:
                    continue
                results.append(
                    UpcomingEvent(
                        code=event.code,
                        name=event.name,
                        impuesto=event.impuesto,
                        period_label=period_label,
                        deadline=deadline,
                        days_until=(deadline - today).days,
                        description=event.description,
                        applicable_to=tuple(s.value for s in event.applicable_to),
                        normativa=event.normativa,
                    )
                )

    results.sort(key=lambda e: (e.deadline, e.code))
    return results


__all__ = [
    "CATALOG",
    "CATALOG_BY_CODE",
    "DeadlineSchedule",
    "FiscalEvent",
    "FiscalYearResolution",
    "Periodicity",
    "RentaCampaignStatus",
    "TaxpayerSegment",
    "UpcomingEvent",
    "get_upcoming_events",
    "resolve_current_fiscal_year",
]
