"""Tests de la API HTTP de demostración."""

from __future__ import annotations

import importlib
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hacienda_ai.api import create_app
from hacienda_ai.api.app import _corpus_fingerprint, _qualitative_confidence
from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import Deduction, ValidationStatus


@pytest.fixture
def client() -> TestClient:
    # DB en memoria por test: aislado, sin tocar `~/.hacienda-ai/hacienda.db`
    # ni dejar archivos colgando entre runs de pytest.
    return TestClient(create_app(db_path=":memory:"))


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


def test_get_deductions_returns_corpus_with_clickable_sources(client: TestClient) -> None:
    """Cada deducción debe ser navegable a su fuente oficial: estatales con
    pinpoint BOE (artículo + ancla #aN); autonómicas con `url` al
    consolidado del boletín correspondiente (BOCM-CM, DOGC, etc.) — el
    `pinpoint_url` BOE no aplica fuera del BOE estatal."""
    r = client.get("/deductions")
    assert r.status_code == 200
    data = r.json()
    assert data["corpus"]["count"] >= 30
    assert data["corpus"]["last_reviewed_at"]
    assert data["corpus"]["engine_version"]
    assert data["disclaimer"]
    for entry in data["deductions"]:
        if entry["scope"] == "estatal":
            anchors = [s for s in entry["sources"] if s.get("pinpoint_url")]
            assert anchors, f"{entry['id']} (estatal) sin pinpoint_url BOE"
            url = anchors[0]["pinpoint_url"]
            assert url.startswith("https://www.boe.es/buscar/act.php?id=BOE-A-")
            assert "#" in url, f"pinpoint URL sin ancla: {url}"
        else:
            navigable = [s for s in entry["sources"] if s.get("url")]
            assert navigable, f"{entry['id']} ({entry['scope']}) sin url al boletín"


def test_get_deductions_pinpoint_url_not_built_for_regional_bulletins(
    client: TestClient,
) -> None:
    """Bug latente arreglado en QW4: el pinpoint_url BOE solo debe
    construirse cuando boe_id apunta al BOE estatal. Si una fuente cita
    BOCM-..., DOGC-..., etc., no se genera URL https://www.boe.es/... con
    ese id (sería un enlace roto)."""
    data = client.get("/deductions").json()
    for entry in data["deductions"]:
        for s in entry["sources"]:
            if s.get("pinpoint_url"):
                assert s["boe_id"].startswith("BOE-A-"), (
                    f"{entry['id']}: pinpoint_url construido para boe_id "
                    f"no-BOE: {s['boe_id']}"
                )


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
    assert r.status_code == 201
    data = r.json()
    assert data["devengo_date"] == "2024-12-31"
    assert data["corpus"]["count"] >= 20
    assert data["disclaimer"]
    statuses = [e["status"] for e in data["evaluations"]]
    assert statuses.count("applies") >= 3, f"applies insuficientes: {statuses}"
    missing = statuses.count("missing_data") + statuses.count("missing_evidence")
    assert missing >= 2, f"missing_* insuficientes: {statuses}"
    assert statuses.count("pending_validation") == 0, f"pending_validation > 0: {statuses}"


def test_evaluation_entries_carry_clickable_source(client: TestClient) -> None:
    """Cada evaluación devuelve al menos una fuente clicable: pinpoint BOE
    para las estatales, o `url` a sede CM para las autonómicas Madrid."""
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    data = client.post("/evaluations", json={"profile_id": pid}).json()
    for ev in data["evaluations"]:
        clickable = [s for s in ev["sources"] if s.get("pinpoint_url") or s.get("url")]
        assert clickable, f"{ev['deduction_id']} sin fuente clicable"


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
    """Con devengo 2024-12-31 (default), cada deducción estatal debe
    declarar la versión LIRPF vigente desde 2022-01-01 sin fecha de fin,
    modificada por BOE-A-2021-21657. Las autonómicas Madrid citan BOCM,
    que aún no tiene Norma registrada en el registry (verificador por
    boletín pendiente), por lo que `applicable_versions` puede estar
    vacío para ellas — eso es honesto, no un bug."""
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    data = client.post("/evaluations", json={"profile_id": pid}).json()
    assert data["devengo_date"] == "2024-12-31"
    estatales = [
        ev for ev in data["evaluations"]
        if any(s.get("boe_id", "").startswith("BOE-A-") for s in ev["sources"])
    ]
    assert estatales, "no se encontraron deducciones estatales en el payload"
    for ev in estatales:
        versions = ev.get("applicable_versions")
        assert versions, f"{ev['deduction_id']} estatal sin applicable_versions"
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
    sample = next(
        ev for ev in data["evaluations"]
        if any(s.get("boe_id", "").startswith("BOE-A-") for s in ev["sources"])
    )
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


def test_qualitative_confidence_thresholds() -> None:
    """Umbrales documentados en `CONFIDENCE_THRESHOLDS`. La función debe
    transmitir el bucket sin sugerir precisión que el motor no tiene."""
    assert _qualitative_confidence(0.95) == "alta"
    assert _qualitative_confidence(0.85) == "alta"
    assert _qualitative_confidence(0.80) == "alta"
    assert _qualitative_confidence(0.799) == "media"
    assert _qualitative_confidence(0.50) == "media"
    assert _qualitative_confidence(0.499) == "baja"
    assert _qualitative_confidence(0.0) == "baja"


def test_evaluation_confidence_is_qualitative_label(client: TestClient) -> None:
    """El payload del API no debe devolver un float pseudo-calibrado; debe
    devolver una etiqueta cualitativa del conjunto cerrado."""
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    data = client.post("/evaluations", json={"profile_id": pid}).json()
    allowed = {"alta", "media", "baja"}
    for ev in data["evaluations"]:
        assert isinstance(ev["confidence"], str), (
            f"{ev['deduction_id']} devuelve confidence numérico: {ev['confidence']!r}"
        )
        assert ev["confidence"] in allowed, (
            f"{ev['deduction_id']} devuelve etiqueta fuera de {allowed}: {ev['confidence']!r}"
        )


def test_qw6_applies_never_returns_zero_amount(client: TestClient) -> None:
    """QW6 — invariante: ninguna evaluación con status `applies` puede tener
    `estimated_amount == 0`. Era el patrón con el que las 23 deducciones
    `manual_review` del corpus se camuflaban en la tabla como "Aplica · 0 €".
    Tras QW6 esos casos salen como `requires_manual_calculation`."""
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    data = client.post("/evaluations", json={"profile_id": pid}).json()
    offenders = [
        ev["deduction_id"]
        for ev in data["evaluations"]
        if ev["status"] == "applies" and ev["estimated_amount"] == 0
    ]
    assert not offenders, (
        f"deducciones con 'applies' + 0 €: {offenders} — "
        "deben migrar a 'requires_manual_calculation'"
    )


def test_qw6_corpus_surfaces_requires_manual_calculation(client: TestClient) -> None:
    """El corpus actual tiene 23 entradas `calculation.type=manual_review`.
    Con cualquier perfil que satisfaga sus requisitos (al menos
    `es_minimo_personal_familiar_general_2024` no tiene requisitos), el API
    debe emitir el nuevo estado. Si esta cuenta cae a 0 inesperadamente,
    o un refactor del motor anula la rama, este test la caza."""
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    data = client.post("/evaluations", json={"profile_id": pid}).json()
    rmc = [
        ev for ev in data["evaluations"]
        if ev["status"] == "requires_manual_calculation"
    ]
    assert rmc, "no aparece ninguna 'requires_manual_calculation' en la respuesta"
    sample = rmc[0]
    # El payload sigue llevando citas pinpoint clicables y `applicable_versions`:
    # la deducción aplica, lo que falta es el cómputo.
    assert sample["sources"], f"{sample['deduction_id']} sin citas"
    assert sample["estimated_amount"] == 0
    assert sample["confidence"] in {"alta", "media", "baja"}


def test_qw6_pdf_renders_requires_manual_calculation_label(client: TestClient) -> None:
    """El PDF firmable tiene que rotular el nuevo estado con texto humano
    en castellano. Si alguien añade un estado al `RuleStatus` literal pero
    olvida la traducción, en el PDF se vería el snake_case crudo."""
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    eid = client.post("/evaluations", json={"profile_id": pid}).json()["evaluation_id"]
    r = client.get(f"/evaluations/{eid}/pdf")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")


def test_qw6_pdf_html_includes_manual_calculation_label_and_class() -> None:
    """Renderizamos el HTML intermedio para inspeccionar etiquetas/CSS sin
    parsear PDF. El estado debe aparecer con label "Requiere cálculo manual"
    y clase CSS dedicada distinta de `status-applies` (color azul, no
    verde) para que el asesor lo distinga al revisar el expediente."""
    from hacienda_ai.api.pdf import render_evaluation_report_html

    evaluation = {
        "evaluation_id": "test-eid",
        "evaluated_at": "2026-05-16T10:00:00+00:00",
        "devengo_date": "2024-12-31",
        "profile": {"tax_year": 2024, "region": "Madrid", "filing_mode": "individual"},
        "corpus": {"count": 1, "last_reviewed_at": "2026-05-16",
                   "engine_version": "0.1.0", "fingerprint_sha256": "x" * 64},
        "disclaimer": "Aviso legal.",
        "evaluations": [{
            "deduction_id": "test_mr", "deduction_name": "Manual review test",
            "status": "requires_manual_calculation", "estimated_amount": 0,
            "reason": "Fórmula no lineal.",
            "missing_fields": [], "missing_documents": [],
            "risk_level": "medium", "confidence": "media",
            "sources": [], "applicable_versions": [],
        }],
    }
    html = render_evaluation_report_html(evaluation)
    assert "Requiere cálculo manual" in html
    assert "status-requires_manual_calculation" in html


def test_safety_module_has_been_removed() -> None:
    """`safety.py` era teatro de cumplimiento: declarado en README y nunca
    invocado por ningún endpoint. Se ha eliminado del paquete; cualquier
    reintroducción debe venir acompañada de uso real en producción."""
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("hacienda_ai.safety")


def test_evaluation_response_carries_evaluation_id_and_profile_snapshot(
    client: TestClient,
) -> None:
    """QW5: cada evaluación se persiste con un evaluation_id estable y lleva
    un snapshot del perfil aplicado, para que el PDF y el lookup posterior
    sean reproducibles aunque el perfil cambie."""
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    data = client.post("/evaluations", json={"profile_id": pid}).json()
    assert isinstance(data["evaluation_id"], str)
    assert len(data["evaluation_id"]) >= 16
    assert data["profile"]["region"] == "Madrid"
    assert data["profile"]["tax_year"] == 2024


def test_corpus_meta_carries_stable_fingerprint(client: TestClient) -> None:
    """QW5: el footer del PDF firmable necesita un SHA-256 agregado del
    corpus determinista entre arranques del proceso (mismo corpus →
    misma firma) y de 64 hex characters."""
    data1 = client.get("/deductions").json()
    data2 = client.get("/deductions").json()
    fp = data1["corpus"]["fingerprint_sha256"]
    assert isinstance(fp, str)
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)
    assert fp == data2["corpus"]["fingerprint_sha256"], (
        "fingerprint_sha256 debe ser estable entre llamadas; obtenido drift"
    )


def test_get_evaluation_round_trip(client: TestClient) -> None:
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    evaluation = client.post("/evaluations", json={"profile_id": pid}).json()
    eid = evaluation["evaluation_id"]
    r = client.get(f"/evaluations/{eid}")
    assert r.status_code == 200
    assert r.json()["evaluation_id"] == eid
    assert r.json()["devengo_date"] == evaluation["devengo_date"]


def test_get_evaluation_unknown_returns_404(client: TestClient) -> None:
    r = client.get("/evaluations/does-not-exist")
    assert r.status_code == 404


def test_evaluation_pdf_renders_with_pdf_headers(client: TestClient) -> None:
    """QW5: el endpoint /evaluations/{id}/pdf devuelve un PDF nativo
    (cabecera mágica %PDF-), con content-type apropiado y
    Content-Disposition de descarga con nombre de archivo legible."""
    pid = client.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
    eid = client.post("/evaluations", json={"profile_id": pid}).json()["evaluation_id"]
    r = client.get(f"/evaluations/{eid}/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    disposition = r.headers["content-disposition"]
    assert "attachment" in disposition
    assert "hacienda-ai-evaluacion-" in disposition
    assert disposition.endswith('.pdf"')
    body = r.content
    assert body.startswith(b"%PDF-"), f"magic header inesperada: {body[:8]!r}"
    assert len(body) > 5000, f"PDF sospechosamente pequeño: {len(body)} bytes"


def test_evaluation_pdf_unknown_id_returns_404(client: TestClient) -> None:
    r = client.get("/evaluations/does-not-exist/pdf")
    assert r.status_code == 404


def test_evaluation_pdf_uses_madrid_profile_with_state_and_regional_sources(
    client: TestClient,
) -> None:
    """Un PDF real (perfil rico Madrid con campos QW4) debe renderizar sin
    error tanto las citas BOE estatales como las BOCM autonómicas."""
    rich = {
        "tax_year": 2024,
        "region": "Madrid",
        "filing_mode": "conjunta",
        "personal": {
            "is_under_35": True,
            "rental_deposit_madrid": True,
            "fosters_elderly_or_disabled_at_home": True,
            "reta_new_alta_this_year": True,
            "gender": "F",
        },
        "family": {
            "children_count": 2,
            "children_young_count": 1,
            "international_adoptions_this_year": 1,
            "births_or_adoptions_this_year": 1,
            "unit_type": "biparental",
        },
        "income": {"work_gross": 30000, "work_net": 27500},
        "expenses": {
            "rental_madrid_youth": 6000,
            "investment_madrid_startups": 5000,
            "donations_madrid_cultural": 200,
            "cultural_consumption_madrid": 800,
            "pension_plan_individual": 2000,
        },
        "documents": ["Libro de familia o certificado de convivencia"],
    }
    pid = client.post("/profiles", json=rich).json()["profile_id"]
    eid = client.post("/evaluations", json={"profile_id": pid}).json()["evaluation_id"]
    r = client.get(f"/evaluations/{eid}/pdf")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")


# --------------------------------------------------------------------------
# Tests de `_corpus_fingerprint` (QW2): la firma del PDF tiene que cubrir
# todos los campos que determinan la recomendación, no solo `(id, tax_year,
# sources)`. Si alguien edita un importe sin tocar las fuentes BOE, o ajusta
# un tope, o cambia `validation_status`, la firma DEBE moverse — si no, el
# PDF "firmado" miente.
# --------------------------------------------------------------------------


def _pick_calculable_deduction() -> Deduction:
    """Devuelve la primera deducción del corpus con `calculation.type ==
    fixed_amount` y `fixed_amount` no nulo, para poder mutar el importe."""
    for d in load_deductions():
        if d.calculation.type == "fixed_amount" and d.calculation.fixed_amount is not None:
            return d
    raise AssertionError("se esperaba al menos una deducción con fixed_amount no nulo")


def test_fingerprint_moves_when_fixed_amount_changes() -> None:
    """Bug original de QW2: cambiar el importe sin tocar las fuentes no
    movía la firma. Ahora debe moverla."""
    deductions = load_deductions()
    base_fp = _corpus_fingerprint(deductions)
    target = _pick_calculable_deduction()
    bumped_calc = replace(target.calculation, fixed_amount=(target.calculation.fixed_amount or 0) + 1.0)
    mutated = [replace(d, calculation=bumped_calc) if d.id == target.id else d for d in deductions]
    assert _corpus_fingerprint(mutated) != base_fp


def test_fingerprint_moves_when_limit_changes() -> None:
    deductions = load_deductions()
    base_fp = _corpus_fingerprint(deductions)
    target = deductions[0]
    new_limit = (target.limit or 0.0) + 100.0
    mutated = [replace(d, limit=new_limit) if d.id == target.id else d for d in deductions]
    assert _corpus_fingerprint(mutated) != base_fp


def test_fingerprint_moves_when_effective_to_changes() -> None:
    """Acortar la vigencia de una deducción cambia la recomendación para
    devengos cercanos al límite. La firma debe reflejarlo."""
    from datetime import date as _date
    deductions = load_deductions()
    base_fp = _corpus_fingerprint(deductions)
    target = next(d for d in deductions if d.effective_to is not None)
    mutated = [
        replace(d, effective_to=_date(target.effective_to.year, 6, 30))
        if d.id == target.id else d
        for d in deductions
    ]
    assert _corpus_fingerprint(mutated) != base_fp


def test_fingerprint_moves_when_validation_status_changes() -> None:
    """Degradar una deducción de `validada` a `dudosa` cambia su estado en
    cualquier evaluación. La firma debe reflejarlo."""
    deductions = load_deductions()
    base_fp = _corpus_fingerprint(deductions)
    target = deductions[0]
    mutated = [
        replace(d, validation_status=ValidationStatus.DUDOSA) if d.id == target.id else d
        for d in deductions
    ]
    assert _corpus_fingerprint(mutated) != base_fp


def test_fingerprint_moves_when_requirements_change() -> None:
    """Añadir un requisito estructurado cambia las condiciones de
    aplicación. La firma debe reflejarlo."""
    from hacienda_ai.models import Requirement
    deductions = load_deductions()
    base_fp = _corpus_fingerprint(deductions)
    target = deductions[0]
    new_reqs = (*target.requirements, Requirement(field="dummy.field", operator="exists"))
    mutated = [replace(d, requirements=new_reqs) if d.id == target.id else d for d in deductions]
    assert _corpus_fingerprint(mutated) != base_fp


def test_fingerprint_stable_under_deduction_reordering() -> None:
    """El orden de la lista de entrada no debe afectar a la firma."""
    deductions = load_deductions()
    reversed_list = list(reversed(deductions))
    assert _corpus_fingerprint(deductions) == _corpus_fingerprint(reversed_list)


def test_fingerprint_stable_across_repeated_calls() -> None:
    """Misma entrada → mismo hash. Reproducibilidad básica."""
    deductions = load_deductions()
    assert _corpus_fingerprint(deductions) == _corpus_fingerprint(deductions)


# --------------------------------------------------------------------------
# Sprint 1 #3: persistencia SQLite. Criterio de aceptación literal:
# crear perfil → reiniciar el server → `GET /profiles/{id}` sigue
# devolviendo el perfil. Imitamos el reinicio cerrando el TestClient y
# construyendo un app nuevo sobre el mismo archivo de DB.
# --------------------------------------------------------------------------


def test_profile_survives_app_restart(tmp_path: Any) -> None:
    """Una vuelta al endpoint sobre un archivo SQLite real: persiste y
    sobrevive a un `create_app()` nuevo. Si esto se rompe, hemos vuelto
    a los `dict` en memoria sin darnos cuenta."""
    db = tmp_path / "restart.db"

    # Arranque 1: creamos un perfil y guardamos su id.
    app_v1 = create_app(db_path=db)
    with TestClient(app_v1) as c1:
        resp = c1.post("/profiles", json={
            "tax_year": 2025,
            "region": "Madrid",
            "family": {"children_count": 1},
            "income": {"work_gross": 30000},
            "documents": ["Libro de familia o certificado de convivencia"],
        })
        assert resp.status_code == 201
        pid = resp.json()["profile_id"]

    # Arranque 2: app nueva, mismo archivo. El perfil debe seguir ahí.
    app_v2 = create_app(db_path=db)
    with TestClient(app_v2) as c2:
        recovered = c2.get(f"/profiles/{pid}")
        assert recovered.status_code == 200
        assert recovered.json()["profile"]["tax_year"] == 2025
        assert recovered.json()["profile"]["region"] == "Madrid"


def test_evaluation_survives_app_restart(tmp_path: Any) -> None:
    """Lo mismo para evaluaciones: tras reiniciar, la evaluación se
    recupera con su `applicable_versions` y `corpus` originales, no se
    re-ejecuta el motor. Importante para el histórico del expediente."""
    db = tmp_path / "restart-eval.db"

    app_v1 = create_app(db_path=db)
    with TestClient(app_v1) as c1:
        pid = c1.post("/profiles", json=_synthetic_profile()).json()["profile_id"]
        eid = c1.post("/evaluations", json={"profile_id": pid}).json()["evaluation_id"]
        original = c1.get(f"/evaluations/{eid}").json()

    app_v2 = create_app(db_path=db)
    with TestClient(app_v2) as c2:
        recovered = c2.get(f"/evaluations/{eid}")
        assert recovered.status_code == 200
        # La evaluación recuperada tiene que ser bit-a-bit la guardada.
        assert recovered.json() == original


def test_create_app_uses_in_memory_db_for_tests() -> None:
    """Las fixtures pasan `db_path=':memory:'`; un test aislado no debe
    contaminar el archivo por defecto en `~/.hacienda-ai/`. Validamos
    que se construye correctamente y los endpoints básicos responden."""
    app = create_app(db_path=":memory:")
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert c.post("/profiles", json={
            "tax_year": 2025,
            "region": "Madrid",
        }).status_code == 201
