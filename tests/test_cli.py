from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hacienda_ai.cli import main


def _write_json(path: Path, payload: Any) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _validated_deduction_payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "test_validada",
        "name": "Deducción validada de prueba",
        "description": "Regla sintética usada solo para probar el CLI.",
        "tax_year": 2025,
        "scope": "estatal",
        "region": None,
        "category": "deduccion",
        "requirements": [{"field": "expenses.test_amount", "operator": ">", "value": 0}],
        "calculation": {"type": "amount_field", "base_field": "expenses.test_amount"},
        "limit": 100.0,
        "taxable_base_limits": {},
        "incompatibilities": [],
        "required_documents": ["Justificante de prueba"],
        "rent_web_boxes": [],
        "sources": [{"type": "test", "title": "Fuente sintética de test", "checked_at": "2026-05-11"}],
        "effective_from": "2025-01-01",
        "effective_to": "2025-12-31",
        "last_reviewed_at": "2026-05-11",
        "risk_level": "bajo",
        "validation_status": "validada",
    }
    data.update(overrides)
    return data


def _profile_payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "tax_year": 2025,
        "region": "Madrid",
        "expenses": {"test_amount": 120.0},
        "documents": ["Justificante de prueba"],
    }
    data.update(overrides)
    return data


def test_cli_evaluate_text_output_lists_applying_deduction(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile = _write_json(tmp_path / "profile.json", _profile_payload())
    deductions = _write_json(tmp_path / "ded.json", [_validated_deduction_payload()])
    exit_code = main(["evaluate", "--profile", str(profile), "--deductions", str(deductions)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Deducciones evaluadas: 1" in captured.out
    assert "Aplica (1)" in captured.out
    assert "test_validada: 100.00" in captured.out


def test_cli_evaluate_json_output_is_parseable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile = _write_json(tmp_path / "profile.json", _profile_payload())
    deductions = _write_json(tmp_path / "ded.json", [_validated_deduction_payload()])
    exit_code = main(["evaluate", "--profile", str(profile), "--deductions", str(deductions), "--format", "json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert isinstance(payload, list)
    assert payload[0]["deduction_id"] == "test_validada"
    assert payload[0]["status"] == "applies"
    assert payload[0]["estimated_amount"] == 100.0


def test_cli_evaluate_reports_missing_data(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile = _write_json(tmp_path / "profile.json", _profile_payload(expenses={}))
    deductions = _write_json(tmp_path / "ded.json", [_validated_deduction_payload()])
    exit_code = main(["evaluate", "--profile", str(profile), "--deductions", str(deductions)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Faltan datos" in captured.out
    assert "Campos faltantes: expenses.test_amount" in captured.out


def test_cli_evaluate_returns_2_on_missing_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["evaluate", "--profile", str(tmp_path / "missing.json")])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "no se encontró el archivo" in captured.err


def test_cli_evaluate_returns_2_on_invalid_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    exit_code = main(["evaluate", "--profile", str(bad)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "JSON inválido" in captured.err


def test_cli_evaluate_returns_2_on_invalid_profile_schema(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile = _write_json(tmp_path / "profile.json", {"tax_year": 2025})  # falta 'region'
    deductions = _write_json(tmp_path / "ded.json", [_validated_deduction_payload()])
    exit_code = main(["evaluate", "--profile", str(profile), "--deductions", str(deductions)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Error de validación" in captured.err


def test_cli_simulate_text_output_shows_three_scenarios(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile = _write_json(tmp_path / "profile.json", _profile_payload())
    deductions = _write_json(tmp_path / "ded.json", [_validated_deduction_payload()])
    exit_code = main(["simulate", "--profile", str(profile), "--deductions", str(deductions)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Simulación fiscal — Ejercicio 2025, Madrid" in captured.out
    assert "Tributación individual" in captured.out
    assert "Tributación conjunta" in captured.out
    assert "conservador" in captured.out
    assert "esperado" in captured.out
    assert "optimizado" in captured.out


def test_cli_simulate_json_output_includes_recommendation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile = _write_json(tmp_path / "profile.json", _profile_payload())
    deductions = _write_json(tmp_path / "ded.json", [_validated_deduction_payload()])
    exit_code = main(["simulate", "--profile", str(profile), "--deductions", str(deductions), "--format", "json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    assert payload["tax_year"] == 2025
    assert payload["recommended_filing_mode"] in {"individual", "conjunta"}
    assert {s["name"] for s in payload["individual"]["scenarios"]} == {
        "conservador",
        "esperado",
        "optimizado",
    }


def test_cli_tax_text_output_shows_cuota_diferencial(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile = _write_json(
        tmp_path / "profile.json",
        {
            "tax_year": 2025,
            "region": "Madrid",
            "personal": {"age": 30},
            "income": {"work_income": 30000.0},
            "withholdings": [{"amount": 4000.0}],
            "taxable_base": {"general": 30000.0, "savings": 0.0},
        },
    )
    deductions = _write_json(tmp_path / "ded.json", [_validated_deduction_payload()])
    exit_code = main(["tax", "--profile", str(profile), "--deductions", str(deductions)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Cuota líquida" in captured.out
    assert "Cuota diferencial" in captured.out
    assert "Base liquidable" in captured.out


def test_cli_tax_json_output_contains_full_breakdown(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile = _write_json(
        tmp_path / "profile.json",
        {
            "tax_year": 2025,
            "region": "Madrid",
            "personal": {"age": 30},
            "income": {"work_income": 30000.0},
            "taxable_base": {"general": 30000.0, "savings": 0.0},
        },
    )
    deductions = _write_json(tmp_path / "ded.json", [_validated_deduction_payload()])
    exit_code = main(["tax", "--profile", str(profile), "--deductions", str(deductions), "--format", "json"])
    captured = capsys.readouterr()
    assert exit_code == 0
    payload = json.loads(captured.out)
    for key in (
        "tax_year",
        "base_imponible_general",
        "minimum_personal_y_familiar",
        "cuota_integra_total",
        "cuota_liquida",
        "cuota_diferencial",
    ):
        assert key in payload, f"falta {key!r} en el JSON"


def test_cli_simulate_returns_2_on_invalid_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    profile = _write_json(tmp_path / "profile.json", {"tax_year": 2025})  # falta region
    deductions = _write_json(tmp_path / "ded.json", [_validated_deduction_payload()])
    exit_code = main(["simulate", "--profile", str(profile), "--deductions", str(deductions)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Error de validación" in captured.err


def test_cli_requires_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "required" in captured.err.lower() or "requerido" in captured.err.lower()
