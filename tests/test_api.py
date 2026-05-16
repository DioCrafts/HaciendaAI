"""Tests de la API HTTP de demostración."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from hacienda_ai.api import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _synthetic_profile() -> dict[str, Any]:
    return {
        "tax_year": 2024,
        "region": "Madrid",
        "filing_mode": "individual",
        "personal": {"has_disability": False},
        "family": {"children_count": 1, "ascendants_count": 0},
        "income": {"work_gross": 30000, "work_net": 27500},
        "expenses": {},
        "documents": ["Libro de familia o certificado de convivencia"],
    }


def test_health_returns_ok(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_root_serves_demo_html(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    body = r.text.lower()
    assert "<html" in body
    assert "haciendaai" in body
    # Form fields the demo relies on
    assert 'name="tax_year"' in body
    assert 'name="region"' in body


def test_get_deductions_returns_corpus_with_pinpoint_urls(client: TestClient) -> None:
    r = client.get("/deductions")
    assert r.status_code == 200
    data = r.json()
    assert data["corpus"]["count"] >= 20
    assert data["corpus"]["last_reviewed_at"]
    assert data["corpus"]["engine_version"]
    assert data["disclaimer"]
    for entry in data["deductions"]:
        anchors = [s for s in entry["sources"] if s.get("pinpoint_url")]
        assert anchors, f"{entry['id']} sin pinpoint_url BOE"
        url = anchors[0]["pinpoint_url"]
        assert url.startswith("https://www.boe.es/buscar/act.php?id=BOE-A-")
        assert "#" in url, f"pinpoint URL sin ancla: {url}"


def test_profile_creation_returns_id_and_normalized_payload(client: TestClient) -> None:
    r = client.post("/profiles", json=_synthetic_profile())
    assert r.status_code == 201
    data = r.json()
    assert isinstance(data["profile_id"], str) and len(data["profile_id"]) >= 16
    assert data["profile"]["tax_year"] == 2024
    assert data["profile"]["region"] == "Madrid"


def test_invalid_profile_returns_422(client: TestClient) -> None:
    bad = _synthetic_profile()
    bad["tax_year"] = "not-a-year"
    r = client.post("/profiles", json=bad)
    assert r.status_code == 422


def test_evaluation_against_unknown_profile_returns_404(client: TestClient) -> None:
    r = client.post("/evaluations", json={"profile_id": "does-not-exist"})
    assert r.status_code == 404


def test_evaluation_requires_profile_id(client: TestClient) -> None:
    r = client.post("/evaluations", json={})
    assert r.status_code == 422


def test_evaluation_returns_expected_distribution(client: TestClient) -> None:
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    r = client.post("/evaluations", json={"profile_id": pid})
    assert r.status_code == 200
    data = r.json()
    assert data["devengo_date"] == "2024-12-31"
    assert data["corpus"]["count"] >= 20
    assert data["disclaimer"]
    statuses = [e["status"] for e in data["evaluations"]]
    assert statuses.count("applies") >= 3, f"applies insuficientes: {statuses}"
    missing = statuses.count("missing_data") + statuses.count("missing_evidence")
    assert missing >= 2, f"missing_* insuficientes: {statuses}"
    assert statuses.count("pending_validation") == 0, f"pending_validation > 0: {statuses}"


def test_evaluation_entries_carry_boe_pinpoint(client: TestClient) -> None:
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    data = client.post("/evaluations", json={"profile_id": pid}).json()
    anchored_total = 0
    for ev in data["evaluations"]:
        boe_sources = [s for s in ev["sources"] if s.get("pinpoint_url")]
        assert boe_sources, f"{ev['deduction_id']} sin pinpoint en evaluación"
        anchored_total += len(boe_sources)
    assert anchored_total >= len(data["evaluations"])


def test_evaluation_response_is_fast(client: TestClient) -> None:
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    data = client.post("/evaluations", json={"profile_id": pid}).json()
    assert data["elapsed_ms"] < 500, f"elapsed_ms={data['elapsed_ms']}"


def test_get_profile_round_trip(client: TestClient) -> None:
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    r = client.get(f"/profiles/{pid}")
    assert r.status_code == 200
    assert r.json()["profile"]["tax_year"] == 2024


def test_get_profile_unknown_returns_404(client: TestClient) -> None:
    r = client.get("/profiles/does-not-exist")
    assert r.status_code == 404
