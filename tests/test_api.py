"""Tests del API HTTP (FastAPI). Usan TestClient — no levantan Uvicorn."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from hacienda_ai.api import app

client = TestClient(app)


def _profile_payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "tax_year": 2025,
        "region": "Madrid",
        "personal": {"age": 28},
        "income": {"work_income": 28000.0},
        "expenses": {"rent_amount": 8000.0, "union_dues_amount": 120.0},
        "documents": [
            "Contrato de arrendamiento y justificantes de pago",
            "Justificante de pago de cuotas sindicales",
        ],
    }
    data.update(overrides)
    return data


# ---------- /health ----------


def test_health_returns_ok() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


# ---------- /v1/deductions ----------


def test_deductions_returns_full_corpus_by_default() -> None:
    response = client.get("/v1/deductions")
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    assert len(items) >= 11  # 11 estatales + 15 autonomicas placeholder
    assert all({"id", "name", "scope", "region", "category", "tax_year"} <= item.keys() for item in items)


def test_deductions_filters_by_region_returns_estatal_plus_that_ccaa() -> None:
    response = client.get("/v1/deductions?region=Madrid")
    assert response.status_code == 200
    items = response.json()
    regions = {item["region"] for item in items}
    # Las estatales tienen region=None; la autonomica de Madrid debe estar; otras no.
    assert regions <= {None, "Madrid"}
    assert any(item["region"] == "Madrid" for item in items)
    assert not any(item["region"] == "Cataluña" for item in items)


def test_deductions_filters_by_tax_year() -> None:
    response = client.get("/v1/deductions?tax_year=2025")
    assert response.status_code == 200
    items = response.json()
    assert all(item["tax_year"] == 2025 for item in items)


def test_deductions_filter_by_unknown_tax_year_returns_empty() -> None:
    response = client.get("/v1/deductions?tax_year=2099")
    assert response.status_code == 200
    assert response.json() == []


# ---------- /v1/evaluate ----------


def test_evaluate_returns_one_result_per_deduction() -> None:
    response = client.post("/v1/evaluate", json=_profile_payload())
    assert response.status_code == 200
    items = response.json()
    assert isinstance(items, list)
    valid_statuses = {"applies", "does_not_apply", "missing_data", "missing_evidence", "pending_validation"}
    for item in items:
        assert item["status"] in valid_statuses
        assert "deduction_id" in item
        assert "estimated_amount" in item


def test_evaluate_returns_400_when_profile_is_invalid() -> None:
    response = client.post("/v1/evaluate", json={"tax_year": 2025})  # falta 'region'
    assert response.status_code == 400
    assert "region" in response.json()["detail"].lower()


def test_evaluate_returns_422_when_body_is_not_an_object() -> None:
    """FastAPI rechaza tipos incorrectos antes de llegar al handler."""
    response = client.post("/v1/evaluate", json=["not", "an", "object"])
    assert response.status_code == 422


def test_evaluate_returns_400_on_invalid_tax_year_type() -> None:
    response = client.post("/v1/evaluate", json={"tax_year": "dosmil25", "region": "Madrid"})
    assert response.status_code == 400


# ---------- /v1/simulate ----------


def test_simulate_returns_full_report() -> None:
    response = client.post("/v1/simulate", json=_profile_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["tax_year"] == 2025
    assert body["region"] == "Madrid"
    assert body["recommended_filing_mode"] in {"individual", "conjunta"}
    assert {"individual", "conjunta"} <= body.keys()
    for filing_key in ("individual", "conjunta"):
        scenarios = body[filing_key]["scenarios"]
        assert {scenario["name"] for scenario in scenarios} == {"conservador", "esperado", "optimizado"}


def test_simulate_returns_400_when_profile_is_invalid() -> None:
    response = client.post("/v1/simulate", json={"tax_year": 2025})
    assert response.status_code == 400


# ---------- /v1/tax ----------


def test_tax_endpoint_returns_comparison_with_savings() -> None:
    response = client.post(
        "/v1/tax",
        json={
            "tax_year": 2025,
            "region": "Madrid",
            "personal": {"age": 30},
            "income": {"work_income": 30000.0},
            "withholdings": [{"amount": 4000.0}],
            "taxable_base": {"general": 30000.0, "savings": 0.0},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert {"with_rules", "without_rules", "ahorro_real", "savings_per_rule"} <= body.keys()
    assert body["with_rules"]["tax_year"] == 2025
    assert body["with_rules"]["base_imponible_general"] == 30000.0
    # Cuota integra general 30000 con mínimo 5550: 7165.5 - 1054.5 = 6111
    assert abs(body["with_rules"]["cuota_integra_general"] - 6111.0) < 0.01
    assert "cuota_diferencial" in body["with_rules"]
    assert isinstance(body["savings_per_rule"], list)


def test_tax_endpoint_returns_400_when_profile_is_invalid() -> None:
    response = client.post("/v1/tax", json={"tax_year": 2025})
    assert response.status_code == 400


# ---------- /v1/opportunities ----------


def test_opportunities_endpoint_returns_sorted_list() -> None:
    response = client.post(
        "/v1/opportunities",
        json={
            "tax_year": 2025,
            "region": "Madrid",
            "personal": {"age": 35},
            "income": {"work_income": 35000.0},
            "taxable_base": {
                "general": 35000.0,
                "savings": 0.0,
                "net_work_and_economic_income": 32000.0,
            },
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) > 0
    savings = [item["potential_savings_estimate"] for item in body]
    assert savings == sorted(savings, reverse=True)


def test_opportunities_endpoint_returns_400_on_invalid_profile() -> None:
    response = client.post("/v1/opportunities", json={"tax_year": 2025})
    assert response.status_code == 400


# ---------- CORS / OpenAPI ----------


def test_cors_allows_origin_header() -> None:
    response = client.options(
        "/v1/evaluate",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "*"


def test_openapi_schema_is_exposed() -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    schema = response.json()
    assert "/v1/evaluate" in schema["paths"]
    assert "/v1/simulate" in schema["paths"]
    assert "/v1/deductions" in schema["paths"]


# ---------- CLI 'serve' (sin levantar uvicorn) ----------


# ---------- Auth ----------


def test_v1_endpoints_are_open_when_api_key_env_var_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HACIENDA_AI_API_KEY", raising=False)
    response = client.post("/v1/evaluate", json=_profile_payload())
    assert response.status_code == 200


def test_v1_endpoints_require_api_key_when_env_var_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HACIENDA_AI_API_KEY", "secret-key")
    response = client.post("/v1/evaluate", json=_profile_payload())
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "ApiKey"


def test_v1_endpoints_accept_correct_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HACIENDA_AI_API_KEY", "secret-key")
    response = client.post(
        "/v1/evaluate",
        headers={"X-API-Key": "secret-key"},
        json=_profile_payload(),
    )
    assert response.status_code == 200


def test_v1_endpoints_reject_wrong_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HACIENDA_AI_API_KEY", "secret-key")
    response = client.post(
        "/v1/evaluate",
        headers={"X-API-Key": "wrong-key"},
        json=_profile_payload(),
    )
    assert response.status_code == 401


def test_health_stays_open_even_when_api_key_is_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HACIENDA_AI_API_KEY", "secret-key")
    response = client.get("/health")
    assert response.status_code == 200


def test_deductions_endpoint_also_requires_key_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HACIENDA_AI_API_KEY", "secret-key")
    assert client.get("/v1/deductions").status_code == 401
    assert client.get("/v1/deductions", headers={"X-API-Key": "secret-key"}).status_code == 200


def test_simulate_endpoint_also_requires_key_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HACIENDA_AI_API_KEY", "secret-key")
    assert client.post("/v1/simulate", json=_profile_payload()).status_code == 401
    response = client.post(
        "/v1/simulate",
        headers={"X-API-Key": "secret-key"},
        json=_profile_payload(),
    )
    assert response.status_code == 200


# ---------- CLI 'serve' (sin levantar uvicorn) ----------


def test_cli_serve_returns_2_when_uvicorn_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Si uvicorn no está instalado, el CLI debe avisar y salir con código 2."""
    import builtins
    import sys

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "uvicorn":
            raise ImportError("simulated absence of uvicorn")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delitem(sys.modules, "uvicorn", raising=False)

    from hacienda_ai.cli import main

    exit_code = main(["serve", "--port", "8123"])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "hacienda-ai[api]" in captured.err
