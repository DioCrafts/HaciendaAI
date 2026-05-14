"""HTTP API que expone el motor de reglas y el simulador.

El módulo se importa solo cuando FastAPI está instalado (extra `[api]`).
El resto del paquete (deducciones, motor, simulador, CLI sin `serve`)
funciona sin ninguna dependencia HTTP.

Autenticación opcional vía header `X-API-Key`: cuando la variable de
entorno `HACIENDA_AI_API_KEY` está definida, los endpoints `/v1/*`
exigen ese header. Si no está definida, la API funciona abierta (útil
en local). El endpoint `/health` queda siempre abierto.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Header, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .deductions import load_deductions
from .models import Deduction, TaxProfile, ValidationError
from .rules import evaluate_deductions
from .simulator import simulate

app = FastAPI(
    title="HaciendaAI API",
    description=("Copiloto Fiscal IRPF España. Motor determinista de reglas. No sustituye a un asesor fiscal."),
    version=__version__,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


API_KEY_ENV_VAR = "HACIENDA_AI_API_KEY"


def verify_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Si HACIENDA_AI_API_KEY está definida, exige el header X-API-Key
    coincidente. Si no, no aplica auth (modo abierto para desarrollo)."""
    expected = os.environ.get(API_KEY_ENV_VAR)
    if not expected:
        return
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida o ausente. Envía el header X-API-Key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/v1/deductions", tags=["corpus"], dependencies=[Depends(verify_api_key)])
def list_deductions(
    region: str | None = None,
    tax_year: int | None = None,
) -> list[dict[str, Any]]:
    """Devuelve un resumen del corpus, opcionalmente filtrado por región
    (combinando estatales + autonómicas de esa CCAA) o ejercicio."""
    deductions = load_deductions()
    filtered = deductions
    if region is not None:
        normalized = region.lower()
        filtered = [d for d in filtered if d.region is None or d.region.lower() == normalized]
    if tax_year is not None:
        filtered = [d for d in filtered if d.tax_year == tax_year]
    return [_deduction_summary(d) for d in filtered]


ProfilePayload = Annotated[dict[str, Any], Body(...)]


@app.post("/v1/evaluate", tags=["motor"], dependencies=[Depends(verify_api_key)])
def evaluate_profile(profile: ProfilePayload) -> list[dict[str, Any]]:
    """Evalúa el corpus completo contra el perfil fiscal recibido."""
    parsed = _parse_profile(profile)
    deductions = load_deductions()
    evaluations = evaluate_deductions(deductions, parsed)
    return [asdict(evaluation) for evaluation in evaluations]


@app.post("/v1/simulate", tags=["motor"], dependencies=[Depends(verify_api_key)])
def simulate_profile(profile: ProfilePayload) -> dict[str, Any]:
    """Genera la simulación conservador / esperado / optimizado para
    tributación individual y conjunta."""
    parsed = _parse_profile(profile)
    deductions = load_deductions()
    return asdict(simulate(deductions, parsed))


def _parse_profile(payload: dict[str, Any]) -> TaxProfile:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="El cuerpo debe ser un objeto JSON con el perfil fiscal.")
    try:
        return TaxProfile.from_dict(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _deduction_summary(deduction: Deduction) -> dict[str, Any]:
    return {
        "id": deduction.id,
        "name": deduction.name,
        "scope": deduction.scope.value,
        "region": deduction.region,
        "category": deduction.category.value,
        "tax_year": deduction.tax_year,
        "validation_status": deduction.validation_status.value,
        "risk_level": deduction.risk_level.value,
    }
