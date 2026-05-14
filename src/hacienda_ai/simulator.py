"""Simulador básico fiscal: escenarios y comparación individual vs conjunta.

El simulador no calcula la cuota IRPF completa; agrega el importe estimado
de las deducciones por escenario para dar una sensibilidad sobre el resultado
en función de los datos y documentos disponibles.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, get_args

from .models import Deduction, TaxProfile
from .rules import evaluate_deductions

ScenarioName = Literal["conservador", "esperado", "optimizado"]
FilingMode = Literal["individual", "conjunta"]

SCENARIO_STATUSES: dict[ScenarioName, frozenset[str]] = {
    "conservador": frozenset({"applies"}),
    "esperado": frozenset({"applies", "missing_evidence"}),
    "optimizado": frozenset({"applies", "missing_evidence", "missing_data"}),
}

SCENARIO_DESCRIPTIONS: dict[ScenarioName, str] = {
    "conservador": "Solo deducciones con requisitos y justificantes ya acreditados.",
    "esperado": "Incluye además deducciones a las que solo faltan justificantes documentales.",
    "optimizado": "Incluye además deducciones a las que falta información del perfil.",
}


@dataclass(frozen=True)
class Scenario:
    name: ScenarioName
    description: str
    filing_mode: FilingMode
    total_estimated_amount: float
    included_deduction_ids: tuple[str, ...]


@dataclass(frozen=True)
class FilingScenarios:
    filing_mode: FilingMode
    scenarios: tuple[Scenario, ...]


@dataclass(frozen=True)
class SimulationReport:
    tax_year: int
    region: str
    requested_filing_mode: str
    individual: FilingScenarios
    conjunta: FilingScenarios
    recommended_filing_mode: FilingMode


def simulate(deductions: list[Deduction], profile: TaxProfile) -> SimulationReport:
    individual = _scenarios_for(deductions, profile, "individual")
    conjunta = _scenarios_for(deductions, profile, "conjunta")

    individual_esperado = _pick(individual, "esperado")
    conjunta_esperado = _pick(conjunta, "esperado")
    recommended: FilingMode = (
        "individual"
        if individual_esperado.total_estimated_amount >= conjunta_esperado.total_estimated_amount
        else "conjunta"
    )

    return SimulationReport(
        tax_year=profile.tax_year,
        region=profile.region,
        requested_filing_mode=profile.filing_mode,
        individual=individual,
        conjunta=conjunta,
        recommended_filing_mode=recommended,
    )


def _scenarios_for(deductions: list[Deduction], profile: TaxProfile, filing_mode: FilingMode) -> FilingScenarios:
    adapted = replace(profile, filing_mode=filing_mode)
    evaluations = evaluate_deductions(deductions, adapted)
    scenarios: list[Scenario] = []
    for name in get_args(ScenarioName):
        statuses = SCENARIO_STATUSES[name]
        included = [evaluation for evaluation in evaluations if evaluation.status in statuses]
        scenarios.append(
            Scenario(
                name=name,
                description=SCENARIO_DESCRIPTIONS[name],
                filing_mode=filing_mode,
                total_estimated_amount=sum(evaluation.estimated_amount for evaluation in included),
                included_deduction_ids=tuple(evaluation.deduction_id for evaluation in included),
            )
        )
    return FilingScenarios(filing_mode=filing_mode, scenarios=tuple(scenarios))


def _pick(filing: FilingScenarios, name: ScenarioName) -> Scenario:
    for scenario in filing.scenarios:
        if scenario.name == name:
            return scenario
    raise KeyError(f"Escenario no encontrado: {name}")
