"""Cobertura mínima por CCAA tras QW5.

QW5 siembra la infraestructura de cinco comunidades autónomas adicionales
a Madrid (Cataluña, Andalucía, Comunitat Valenciana, Galicia, Castilla y
León) sin meter importes inventados: cada catálogo arranca con dos
deducciones representativas (nacimiento/adopción y alquiler joven)
marcadas `validation_status="pendiente_fuente"`. El motor las devuelve
como `pending_validation`, lo que mantiene el corpus auditable y
permite que el catálogo crezca a medida que un fiscalista colegiado
firme cada regla.

Estos tests verifican el contrato estructural:

1. Cada CCAA seeded tiene su norma raíz registrada en el `NormaRegistry`.
2. Cada CCAA seeded tiene al menos dos deducciones en el corpus.
3. Las deducciones seeded son honestas: estado `pendiente_fuente`,
   `risk_level="alto"`, `calculation.type="manual_review"`.
4. El motor las reporta como `pending_validation` con confianza baja
   sobre un perfil real de esa CCAA — NO como `applies` con un cero, NO
   como `requires_manual_calculation` (eso sería pretender que la
   deducción está validada y solo falta el cálculo).
5. Los identificadores de boletín siguen el patrón reconocido (`DOGC-`,
   `BOJA-`, `DOCV-`, `DOG-`, `BOCYL-`), de modo que el chequeo regional
   del cron los toma en cuenta.
"""

from __future__ import annotations

import pytest

from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import TaxProfile, ValidationStatus
from hacienda_ai.normas import load_norma_registry
from hacienda_ai.rules import evaluate_deductions

# (region, boe_id raíz, prefijo esperado del id de deducción)
SEEDED_CCAA: list[tuple[str, str, str]] = [
    ("Cataluña", "DOGC-2024-1", "cat_"),
    ("Andalucía", "BOJA-2024-1", "and_"),
    ("Comunitat Valenciana", "DOCV-2024-1", "val_"),
    ("Galicia", "DOG-2024-1", "gal_"),
    ("Castilla y León", "BOCYL-2024-1", "cyl_"),
]


@pytest.mark.parametrize("region,norma_id,prefix", SEEDED_CCAA)
def test_norma_root_is_registered(region: str, norma_id: str, prefix: str) -> None:
    registry = load_norma_registry()
    assert registry.knows(norma_id), (
        f"{region}: norma raíz {norma_id} no registrada en NormaRegistry"
    )


@pytest.mark.parametrize("region,norma_id,prefix", SEEDED_CCAA)
def test_corpus_contains_at_least_two_seed_deductions(
    region: str, norma_id: str, prefix: str
) -> None:
    deductions = load_deductions()
    region_deds = [d for d in deductions if d.region == region]
    assert len(region_deds) >= 2, (
        f"{region}: esperaba ≥2 deducciones sembradas, hay {len(region_deds)}"
    )
    assert all(d.id.startswith(prefix) for d in region_deds), (
        f"{region}: ids deben empezar por '{prefix}': "
        f"{[d.id for d in region_deds]}"
    )


@pytest.mark.parametrize("region,norma_id,prefix", SEEDED_CCAA)
def test_seed_deductions_are_honestly_pending(
    region: str, norma_id: str, prefix: str
) -> None:
    """Las deducciones sembradas en QW5 NO deben presentarse como
    validadas: el motor las trata como `pending_validation` y el LLM,
    al consultarlas vía `get_deduction_catalog`, ve el estado honesto."""
    deductions = load_deductions()
    region_deds = [d for d in deductions if d.region == region]
    for d in region_deds:
        assert d.validation_status == ValidationStatus.PENDIENTE_FUENTE, (
            f"{d.id}: estado {d.validation_status.value} — los seeds QW5 "
            "deben ir como `pendiente_fuente` hasta firma de fiscalista"
        )
        assert d.risk_level.value == "alto", (
            f"{d.id}: risk_level={d.risk_level.value} — los seeds QW5 deben "
            "marcarse de alto riesgo por no estar validados"
        )
        assert d.calculation.type == "manual_review", (
            f"{d.id}: calculation.type={d.calculation.type} — los seeds QW5 "
            "no deben pretender modelar la fórmula antes de la firma"
        )
        # La cita debe apuntar a la norma raíz registrada.
        assert any(s.boe_id == norma_id for s in d.sources), (
            f"{d.id} no cita la norma raíz {norma_id}"
        )


@pytest.mark.parametrize("region,norma_id,prefix", SEEDED_CCAA)
def test_engine_marks_seed_deductions_as_pending_validation(
    region: str, norma_id: str, prefix: str
) -> None:
    """Sobre un perfil con nacimiento + alquiler en la CCAA, las
    deducciones seeded aparecen como `pending_validation` (NO `applies`,
    NO `requires_manual_calculation`): el motor protege al usuario de
    cifras no firmadas."""
    deductions = load_deductions()
    registry = load_norma_registry()
    profile = TaxProfile.from_dict(
        {
            "tax_year": 2024,
            "region": region,
            "filing_mode": "individual",
            "personal": {"age": 30, "has_disability": False},
            "family": {
                "children_count": 1,
                "ascendants_count": 0,
                "births_or_adoptions_this_year": 1,
            },
            "income": {"work_gross": 28000, "work_net": 25500},
            "expenses": {"housing_rent": 6000},
            "documents": [
                "Libro de familia o resolución de adopción",
                "Contrato de arrendamiento",
                "Justificantes de pago",
            ],
        }
    )
    evaluations = evaluate_deductions(deductions, profile, registry)
    region_evs = [
        ev
        for ev in evaluations
        if ev.deduction_id.startswith(prefix)
    ]
    assert region_evs, f"{region}: no se evaluó ninguna deducción del prefijo {prefix}"
    for ev in region_evs:
        assert ev.status == "pending_validation", (
            f"{region}/{ev.deduction_id}: estado {ev.status} — esperaba "
            "`pending_validation` por estar `pendiente_fuente`"
        )
        assert ev.estimated_amount == 0.0
        assert ev.confidence < 0.5


def test_total_corpus_size_grew_after_qw5() -> None:
    """Cifra agregada: el corpus debe haber crecido al menos en 10
    entradas (5 CCAA × 2 deducciones seed). Esto previene que un
    revert silencioso baje la cobertura sin que nos enteremos."""
    deductions = load_deductions()
    seeded = sum(1 for d in deductions if d.validation_status.value == "pendiente_fuente")
    assert seeded >= 10, (
        f"Esperaba ≥10 deducciones `pendiente_fuente` tras QW5, hay {seeded}"
    )
