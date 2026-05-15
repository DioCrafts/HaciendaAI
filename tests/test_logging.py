"""Tests del logging RGPD: configuración, formato JSON y garantía de
que NO se filtran importes ni texto sensible en los registros."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from hacienda_ai.deductions import load_deductions
from hacienda_ai.logging_setup import (
    LOG_FORMAT_ENV,
    LOG_LEVEL_ENV,
    LOGGER_NAME,
    configure_logging,
    get_logger,
    hash_region,
)
from hacienda_ai.models import TaxProfile
from hacienda_ai.rules import evaluate_deductions


def _profile(**overrides: Any) -> TaxProfile:
    data: dict[str, Any] = {
        "tax_year": 2025,
        "region": "Madrid",
        "personal": {"professional_association_required": True},
        "income": {"work_income": 35000.0},
        "taxable_base": {"net_work_and_economic_income": 32000.0, "liquidable": 28000.0},
        "expenses": {
            "union_dues_amount": 220.0,
            "professional_association_fees_amount": 400.0,
            "pension_plan_contribution_amount": 1800.0,
        },
        "documents": [
            "Justificante de pago de cuotas sindicales",
            "Justificante de cuotas colegiales",
            "Certificado de aportación al plan de pensiones",
        ],
    }
    data.update(overrides)
    return TaxProfile.from_dict(data)


# ---------- configure_logging ----------


def test_configure_logging_is_idempotent() -> None:
    configure_logging(level="INFO", fmt="text")
    handlers_first = list(logging.getLogger(LOGGER_NAME).handlers)
    configure_logging(level="INFO", fmt="text")
    handlers_second = list(logging.getLogger(LOGGER_NAME).handlers)
    assert len(handlers_first) == len(handlers_second) == 1


def test_configure_logging_respects_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LOG_LEVEL_ENV, "DEBUG")
    monkeypatch.setenv(LOG_FORMAT_ENV, "json")
    configure_logging()
    logger = logging.getLogger(LOGGER_NAME)
    assert logger.level == logging.DEBUG


def test_get_logger_returns_child_of_namespace() -> None:
    child = get_logger("rules")
    assert child.name == f"{LOGGER_NAME}.rules"


# ---------- hash_region ----------


def test_hash_region_is_deterministic_within_process() -> None:
    a = hash_region("Madrid")
    b = hash_region("Madrid")
    assert a == b


def test_hash_region_distinguishes_regions() -> None:
    assert hash_region("Madrid") != hash_region("Cataluña")


def test_hash_region_is_case_insensitive_and_trim() -> None:
    assert hash_region(" Madrid ") == hash_region("madrid")


def test_hash_region_returns_none_for_empty() -> None:
    assert hash_region(None) == "none"
    assert hash_region("") == "none"


def test_hash_region_output_is_8_hex_chars() -> None:
    digest = hash_region("Andalucía")
    assert len(digest) == 8
    int(digest, 16)  # raises si no es hex


# ---------- ausencia de PII en los logs del motor ----------


def _capture_logs(profile: TaxProfile, *, json_format: bool = True, level: str = "DEBUG") -> str:
    """Captura los logs del motor.

    Por defecto serializa con JsonFormatter para que las aserciones puedan
    inspeccionar también los campos `extra`. Los tests que verifican el
    formato text pueden pasar `json_format=False`.
    """
    configure_logging(level=level, fmt="json" if json_format else "text")
    logger = logging.getLogger(LOGGER_NAME)
    buffer: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            buffer.append(self.format(record))

    capture = _Capture()
    from hacienda_ai.logging_setup import _JsonFormatter

    capture.setFormatter(_JsonFormatter() if json_format else logging.Formatter("%(message)s"))
    logger.addHandler(capture)
    try:
        evaluate_deductions(load_deductions(), profile)
    finally:
        logger.removeHandler(capture)
    return "\n".join(buffer)


SENSITIVE_LITERALS = [
    "35000",  # income.work_income
    "220",  # union dues
    "32000",  # net_work_and_economic_income
    "28000",  # liquidable
    "1800",  # pension contribution
    "Madrid",  # raw region name
    "Justificante",  # document text
]


def test_logs_do_not_leak_profile_amounts_or_names() -> None:
    profile = _profile()
    logs = _capture_logs(profile)
    for needle in SENSITIVE_LITERALS:
        assert needle not in logs, f"Encontrado literal sensible {needle!r} en logs.\nLogs:\n{logs}"


def test_logs_include_aggregate_counts_and_region_hash() -> None:
    profile = _profile()
    logs = _capture_logs(profile)
    assert "evaluate_started" in logs
    assert "evaluate_finished" in logs
    # El hash de la región aparece, no su nombre.
    assert hash_region("Madrid") in logs


def test_logs_in_json_format_are_one_object_per_line() -> None:
    profile = _profile()
    logs = _capture_logs(profile, level="INFO")
    lines = [line for line in logs.splitlines() if line.strip()]
    assert lines, "esperado al menos un evento JSON"
    for line in lines:
        parsed = json.loads(line)
        assert "event" in parsed
        assert "level" in parsed
        assert "logger" in parsed
    events = {json.loads(line)["event"] for line in lines}
    assert {"evaluate_started", "evaluate_finished"} <= events


def test_rule_evaluated_only_emitted_at_debug_level() -> None:
    profile = _profile()
    logs_debug = _capture_logs(profile, level="DEBUG")
    logs_info = _capture_logs(profile, level="INFO")
    assert "rule_evaluated" in logs_debug
    assert "rule_evaluated" not in logs_info


def test_rule_evaluated_does_not_log_amounts() -> None:
    profile = _profile()
    logs = _capture_logs(profile, level="DEBUG")
    # rule_evaluated incluye has_amount (booleano), nunca el importe.
    assert "estimated_amount" not in logs
    assert "has_amount" in logs
