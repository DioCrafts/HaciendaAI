"""Tests del calendario fiscal y la resolución temporal del ejercicio.

Cubre:

1. `resolve_current_fiscal_year`: fechas representativas a lo largo del
   año (enero antes de campaña, abril en campaña, junio cierre, julio
   tras cierre, diciembre planificación). Verifica el contenido de
   `RentaCampaignStatus`.
2. `get_upcoming_events`: ventana corta y larga, filtros por segmento,
   ordenación por fecha, días hasta el plazo.
3. `_compute_deadlines` (vía catálogo): correctitud de cada
   `DeadlineSchedule`, shift a día hábil cuando cae en fin de semana,
   manejo de bisiestos en el modelo 347.
"""

from __future__ import annotations

from datetime import date

import pytest

from hacienda_ai.fiscal_calendar import (
    CATALOG,
    CATALOG_BY_CODE,
    DeadlineSchedule,
    Periodicity,
    TaxpayerSegment,
    _compute_deadlines,
    _shift_to_next_business_day,
    get_upcoming_events,
    resolve_current_fiscal_year,
)

# ---------- resolve_current_fiscal_year ----------


def test_resolver_in_january_before_renta_opens() -> None:
    """15-ene-2026: campaña 2025 (abre 1-abr-2026, declara ejercicio 2025)
    aún no abierta. `last_declarable_year` es 2024 (último cuya campaña
    ya cerró). `recommended_for_irpf_query` = 2025 (ejercicio recién
    cerrado por devengo, sobre el que el usuario probablemente
    pregunta aunque no pueda presentar todavía)."""
    res = resolve_current_fiscal_year(date(2026, 1, 15))
    assert res.in_progress_year == 2026
    assert res.last_closed_year == 2025
    assert res.last_declarable_year == 2024
    assert res.recommended_for_irpf_query == 2025
    assert res.recommended_devengo == date(2025, 12, 31)
    # La campaña referenciada es la que se abrirá en breve.
    assert res.renta_campaign.tax_year == 2025
    assert res.renta_campaign.opened_at == date(2026, 4, 1)
    assert res.renta_campaign.closed_at == date(2026, 6, 30)
    assert res.renta_campaign.is_before_open


def test_resolver_in_active_renta_campaign() -> None:
    """17-may-2026: campaña activa para el ejercicio 2025."""
    res = resolve_current_fiscal_year(date(2026, 5, 17))
    assert res.in_progress_year == 2026
    assert res.last_closed_year == 2025
    assert res.last_declarable_year == 2025
    assert res.recommended_for_irpf_query == 2025
    assert res.recommended_devengo == date(2025, 12, 31)
    assert res.renta_campaign.tax_year == 2025
    assert res.renta_campaign.is_open
    assert not res.renta_campaign.is_after_close


def test_resolver_on_renta_campaign_open_day() -> None:
    """1-abr-2026: primer día de campaña — debe considerarse abierta."""
    res = resolve_current_fiscal_year(date(2026, 4, 1))
    assert res.renta_campaign.is_open
    assert res.last_declarable_year == 2025


def test_resolver_on_renta_campaign_close_day() -> None:
    """30-jun-2026: último día de campaña — sigue abierta."""
    res = resolve_current_fiscal_year(date(2026, 6, 30))
    assert res.renta_campaign.is_open
    assert not res.renta_campaign.is_after_close


def test_resolver_after_renta_campaign_close() -> None:
    """1-jul-2026: campaña recién cerrada. El ejercicio 2025 sigue siendo
    el último declarable (se acaba de declarar)."""
    res = resolve_current_fiscal_year(date(2026, 7, 1))
    assert res.renta_campaign.is_after_close
    assert res.last_declarable_year == 2025
    assert res.recommended_for_irpf_query == 2025


def test_resolver_in_december_planning() -> None:
    """15-dic-2026: ejercicio 2026 en curso (devengo en 16 días). Último
    declarable: 2025 (campaña ya cerrada en junio)."""
    res = resolve_current_fiscal_year(date(2026, 12, 15))
    assert res.in_progress_year == 2026
    assert res.last_closed_year == 2025
    assert res.last_declarable_year == 2025
    assert res.renta_campaign.is_after_close


def test_resolver_defaults_to_today_when_none(monkeypatch) -> None:
    """Sin argumento, usa `date.today()`."""
    res = resolve_current_fiscal_year()
    today = date.today()
    assert res.today == today
    assert res.in_progress_year == today.year


def test_resolver_dict_serialization_roundtrip() -> None:
    res = resolve_current_fiscal_year(date(2026, 5, 17))
    payload = res.to_dict()
    assert payload["today"] == "2026-05-17"
    assert payload["in_progress_year"] == 2026
    assert payload["recommended_for_irpf_query"] == 2025
    assert payload["recommended_devengo"] == "2025-12-31"
    assert payload["renta_campaign"]["is_open"] is True


# ---------- _compute_deadlines ----------


def test_compute_quarterly_t_20_4t_30_jan_returns_four_dates() -> None:
    """Para 303/130/349 (4T → 30-ene del año siguiente)."""
    deadlines = _compute_deadlines(
        DeadlineSchedule.QUARTERLY_T_20_4T_30_JAN, 2025
    )
    assert len(deadlines) == 4
    dates_only = [d for d, _ in deadlines]
    labels = [label for _, label in deadlines]
    # Las fechas naturales son 20-abr, 20-jul, 20-oct, 30-ene+1.
    # Tras shift al lunes siguiente si cae fin de semana.
    assert labels == ["1T 2025", "2T 2025", "3T 2025", "4T 2025"]
    # Comprobamos meses y que están ordenadas crecientes.
    assert [d.month for d in dates_only] == [4, 7, 10, 1]
    assert dates_only == sorted(dates_only)


def test_compute_quarterly_t_20_4t_4t_january_next_year() -> None:
    """4T del 303 (2025) se presenta el 30-ene-2026."""
    deadlines = _compute_deadlines(
        DeadlineSchedule.QUARTERLY_T_20_4T_30_JAN, 2025
    )
    fourth_q = deadlines[-1]
    assert fourth_q[1] == "4T 2025"
    assert fourth_q[0].year == 2026
    # 30-ene-2026 es viernes → no se mueve.
    assert fourth_q[0] == date(2026, 1, 30)


def test_compute_annual_renta_apr_jun_uses_next_year() -> None:
    """Renta del ejercicio 2025 vence el 30-jun-2026."""
    deadlines = _compute_deadlines(
        DeadlineSchedule.ANNUAL_RENTA_APR_JUN, 2025
    )
    assert len(deadlines) == 1
    deadline, label = deadlines[0]
    assert label == "Ejercicio 2025"
    # 30-jun-2026 es martes — sin shift.
    assert deadline == date(2026, 6, 30)


def test_compute_annual_feb_28_handles_leap_year() -> None:
    """347 del ejercicio 2027 se presenta el 29-feb-2028 (bisiesto)."""
    deadlines = _compute_deadlines(DeadlineSchedule.ANNUAL_FEB_28, 2027)
    deadline, _ = deadlines[0]
    assert deadline == date(2028, 2, 29)


def test_compute_payment_is_apr_oct_dec_returns_three() -> None:
    deadlines = _compute_deadlines(
        DeadlineSchedule.PAYMENT_IS_APR_OCT_DEC_20, 2025
    )
    assert len(deadlines) == 3
    labels = [label for _, label in deadlines]
    assert labels == ["1P 2025", "2P 2025", "3P 2025"]


def test_shift_to_next_business_day_moves_weekend() -> None:
    saturday = date(2025, 4, 19)  # sábado
    sunday = date(2025, 4, 20)  # domingo
    monday = date(2025, 4, 21)
    assert _shift_to_next_business_day(saturday) == monday
    assert _shift_to_next_business_day(sunday) == monday
    assert _shift_to_next_business_day(monday) == monday  # ya hábil


def test_shift_to_next_business_day_in_real_deadline_303() -> None:
    """El 20-abr-2025 es domingo: el 303 del 1T-2025 vence en lunes 21."""
    deadlines = _compute_deadlines(
        DeadlineSchedule.QUARTERLY_T_20_4T_30_JAN, 2025
    )
    first_q_deadline, _ = deadlines[0]
    assert first_q_deadline == date(2025, 4, 21)


# ---------- get_upcoming_events ----------


def test_upcoming_events_in_active_renta_returns_100() -> None:
    """17-may-2026: el modelo 100 (Renta 2025) está a ~44 días."""
    events = get_upcoming_events(date(2026, 5, 17), window_days=60)
    codes = [e.code for e in events]
    assert "100" in codes
    renta = next(e for e in events if e.code == "100")
    assert renta.period_label == "Ejercicio 2025"
    assert renta.deadline == date(2026, 6, 30)
    assert renta.days_until == 44


def test_upcoming_events_filters_by_segment_particular() -> None:
    """Un particular sin actividad NO presenta 303/111/130."""
    events = get_upcoming_events(
        date(2026, 5, 17),
        window_days=365,
        segments=[TaxpayerSegment.PARTICULAR],
    )
    codes = {e.code for e in events}
    assert "100" in codes
    assert "720" in codes  # particular puede tener bienes en extranjero
    assert "303" not in codes
    assert "111" not in codes
    assert "130" not in codes
    assert "200" not in codes


def test_upcoming_events_filters_by_segment_autonomo() -> None:
    events = get_upcoming_events(
        date(2026, 5, 17),
        window_days=365,
        segments=[TaxpayerSegment.AUTONOMO],
    )
    codes = {e.code for e in events}
    # Un autónomo en estimación directa presenta 100, 130, 303, 111, 115.
    assert {"100", "130", "303", "111", "115", "390", "190"}.issubset(codes)
    assert "131" not in codes  # módulos, otro segmento
    assert "200" not in codes  # sociedad
    assert "202" not in codes


def test_upcoming_events_sorted_by_deadline_asc() -> None:
    events = get_upcoming_events(date(2026, 5, 17), window_days=120)
    deadlines = [e.deadline for e in events]
    assert deadlines == sorted(deadlines)


def test_upcoming_events_empty_window_returns_empty() -> None:
    """window_days=0: solo deadlines que sean exactamente hoy."""
    today = date(2026, 5, 17)
    events = get_upcoming_events(today, window_days=0)
    # En esa fecha concreta no hay deadlines.
    assert events == []


def test_upcoming_events_negative_window_raises() -> None:
    with pytest.raises(ValueError, match="window_days"):
        get_upcoming_events(date(2026, 5, 17), window_days=-1)


def test_upcoming_events_empty_segments_returns_empty() -> None:
    """`segments=[]` filtra a nada."""
    events = get_upcoming_events(
        date(2026, 5, 17),
        window_days=365,
        segments=[],
    )
    assert events == []


def test_upcoming_events_default_today_uses_date_today() -> None:
    events = get_upcoming_events(window_days=365)
    # Todos los deadlines deben ser >= hoy.
    today = date.today()
    for e in events:
        assert e.deadline >= today


def test_upcoming_events_days_until_is_correct() -> None:
    today = date(2026, 5, 17)
    events = get_upcoming_events(today, window_days=120)
    for e in events:
        assert e.days_until == (e.deadline - today).days
        assert e.days_until >= 0


def test_upcoming_event_dict_serialization() -> None:
    today = date(2026, 5, 17)
    events = get_upcoming_events(today, window_days=60)
    payload = events[0].to_dict()
    assert payload["code"]
    assert payload["deadline"]
    assert isinstance(payload["applicable_to"], list)
    assert isinstance(payload["normativa"], list)


# ---------- Catálogo ----------


def test_catalog_has_expected_models() -> None:
    """Verifica la presencia de los modelos clave del calendario AEAT."""
    expected = {"100", "130", "131", "303", "390", "111", "115", "190",
                "202", "200", "347", "349", "720", "721"}
    assert expected.issubset(set(CATALOG_BY_CODE.keys()))


def test_catalog_codes_are_unique() -> None:
    codes = [e.code for e in CATALOG]
    assert len(codes) == len(set(codes))


def test_catalog_every_event_has_normativa_or_explanation() -> None:
    """Cada modelo debe llevar referencia normativa para auditoría."""
    for event in CATALOG:
        # Aceptamos descripción explícita en su lugar (no todos los
        # modelos tienen una norma pinpoint trivial); pero exigimos
        # que al menos uno de los dos sea no-vacío.
        assert event.description.strip()
        if event.code in {"100", "130", "131", "303", "390",
                          "111", "115", "190", "202", "200"}:
            assert event.normativa, f"{event.code} sin normativa"


def test_all_catalog_periodicities_are_valid() -> None:
    for event in CATALOG:
        assert event.periodicity in {p for p in Periodicity}
