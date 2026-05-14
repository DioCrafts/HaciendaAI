"""HTTP API que expone el motor de reglas y el simulador.

El módulo se importa solo cuando FastAPI está instalado (extra `[api]`).
El resto del paquete (deducciones, motor, simulador, CLI sin `serve`)
funciona sin ninguna dependencia HTTP.

Sin autenticación: pensado para despliegues locales o detrás de un
proxy/gateway que añada la capa de seguridad. No exponer directamente
a internet sin añadir auth.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException
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


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/v1/deductions", tags=["corpus"])
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


@app.post("/v1/evaluate", tags=["motor"])
def evaluate_profile(profile: ProfilePayload) -> list[dict[str, Any]]:
    """Evalúa el corpus completo contra el perfil fiscal recibido."""
    parsed = _parse_profile(profile)
    deductions = load_deductions()
    evaluations = evaluate_deductions(deductions, parsed)
    return [asdict(evaluation) for evaluation in evaluations]


@app.post("/v1/simulate", tags=["motor"])
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
