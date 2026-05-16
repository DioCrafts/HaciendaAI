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


def test_evaluation_payload_carries_applicable_versions_for_current_devengo(
    client: TestClient,
) -> None:
    """Con devengo 2024-12-31 (default), cada deducción del corpus debe
    declarar la versión LIRPF vigente desde 2022-01-01 sin fecha de fin,
    modificada por BOE-A-2021-21657."""
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    data = client.post("/evaluations", json={"profile_id": pid}).json()
    assert data["devengo_date"] == "2024-12-31"
    for ev in data["evaluations"]:
        versions = ev.get("applicable_versions")
        assert versions, f"{ev['deduction_id']} sin applicable_versions"
        lirpf = next(
            (v for v in versions if v["boe_id"] == "BOE-A-2006-20764"), None
        )
        assert lirpf is not None, f"{ev['deduction_id']} sin versión LIRPF aplicable"
        assert lirpf["effective_from"] == "2022-01-01"
        assert lirpf["effective_to"] is None
        assert lirpf["status"] == "vigente"
        assert lirpf["modified_by_boe_id"] == "BOE-A-2021-21657"


def test_evaluation_payload_uses_historical_version_for_past_devengo(
    client: TestClient,
) -> None:
    """Con un perfil cuyo devengo cae en 2018, la versión aplicable debe
    ser la redacción Ley 26/2014 (2015-01-01 a 2021-12-31), no la actual."""
    historical = {
        "tax_year": 2018,
        "region": "Madrid",
        "devengo_date": "2018-06-30",
        "filing_mode": "individual",
        "personal": {},
        "family": {"children_count": 1, "ascendants_count": 0},
        "income": {"work_gross": 30000, "work_net": 27500},
        "expenses": {},
        "documents": [],
    }
    pid = client.post("/profiles", json=historical).json()["profile_id"]
    data = client.post("/evaluations", json={"profile_id": pid}).json()
    assert data["devengo_date"] == "2018-06-30"
    sample = next(iter(data["evaluations"]))
    versions = sample["applicable_versions"]
    lirpf = next(v for v in versions if v["boe_id"] == "BOE-A-2006-20764")
    assert lirpf["effective_from"] == "2015-01-01"
    assert lirpf["effective_to"] == "2021-12-31"
    assert lirpf["modified_by_boe_id"] == "BOE-A-2014-12328"


def test_evaluation_payload_versions_deduplicated_per_deduction(
    client: TestClient,
) -> None:
    """Aunque una deducción cite varios artículos del mismo BOE
    (planes de pensiones cita arts. 51 y 52 de la LIRPF), el payload
    devuelve una sola versión por norma."""
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    data = client.post("/evaluations", json={"profile_id": pid}).json()
    for ev in data["evaluations"]:
        boe_ids = [v["boe_id"] for v in ev["applicable_versions"]]
        assert len(boe_ids) == len(set(boe_ids)), (
            f"{ev['deduction_id']} repite versiones: {boe_ids}"
        )


def test_deductions_payload_does_not_carry_applicable_versions(
    client: TestClient,
) -> None:
    """Las versiones aplicables dependen del devengo del perfil; el endpoint
    GET /deductions (sin perfil) no debe inventarlas."""
    data = client.get("/deductions").json()
    for entry in data["deductions"]:
        assert "applicable_versions" not in entry
