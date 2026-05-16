from __future__ import annotations

import pytest

from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import Deduction, TaxProfile, ValidationError
from hacienda_ai.rules import evaluate_deduction


def validated_deduction(**overrides):
    data = {
        "id": "test_validada",
        "name": "Deducción validada de prueba",
        "description": "Regla sintética usada solo para probar el motor determinista.",
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
        "sources": [
            {
                "kind": "ley",
                "title": "LIRPF (test sintético)",
                "url": "https://www.boe.es/buscar/act.php?id=BOE-A-2006-20764",
                "article": "art. 1 (test)",
                "paragraph": None,
                "boe_id": "BOE-A-2006-20764",
                "content_hash": "a" * 64,
                "checked_at": "2026-05-11",
            }
        ],
        "effective_from": "2025-01-01",
        "effective_to": "2025-12-31",
        "last_reviewed_at": "2026-05-11",
        "risk_level": "bajo",
        "validation_status": "validada",
    }
    data.update(overrides)
    return Deduction.from_dict(data)


def profile(**overrides):
    data = {
        "tax_year": 2025,
        "region": "Madrid",
        "expenses": {"test_amount": 120.0},
        "documents": ["Justificante de prueba"],
    }
    data.update(overrides)
    return TaxProfile.from_dict(data)


def test_loads_validated_irpf_state_seed():
    """El corpus semilla 2024 estatal carga ≥20 deducciones, todas validadas
    y con al menos una fuente BOE-anclada (boe_id + content_hash)."""
    deductions = load_deductions()
    assert len(deductions) >= 20, f"Se esperaban ≥20 deducciones, hay {len(deductions)}"
    assert all(d.validation_status.value == "validada" for d in deductions), (
        "Hay deducciones no validadas en el corpus semilla"
    )
    for d in deductions:
        anchors = [s for s in d.sources if s.boe_id and s.boe_id.startswith("BOE-A-") and s.content_hash]
        assert anchors, f"Deducción {d.id} no tiene fuente BOE-anclada con content_hash"
    ids = [d.id for d in deductions]
    assert len(ids) == len(set(ids)), "Hay ids duplicados en el corpus"


def test_rejects_deduction_without_source():
    with pytest.raises(ValidationError, match="al menos una fuente"):
        validated_deduction(sources=[])


def test_rejects_unsupported_operator():
    with pytest.raises(ValidationError, match="Operador"):
        validated_deduction(requirements=[{"field": "x", "operator": "contains", "value": 1}])


def test_non_validada_deduction_is_not_recommended_directly():
    """Una deducción en `pendiente_fuente` se rechaza como `pending_validation`
    aunque sus requisitos se cumplan."""
    pending = validated_deduction(
        validation_status="pendiente_fuente",
        sources=[
            {
                "kind": "pendiente_validacion",
                "title": "Por contrastar",
                "url": None,
                "checked_at": None,
            }
        ],
    )
    result = evaluate_deduction(pending, profile())
    assert result.status == "pending_validation"
    assert result.estimated_amount == 0.0


def test_synthetic_profile_yields_multiple_applies_and_missing():
    """Criterio de aceptación QW2: un perfil sintético sobre el corpus 2024 da
    al menos 3 `applies`, al menos 2 `missing_data`/`missing_evidence` y cero
    `pending_validation`."""
    deductions = load_deductions()
    synth = TaxProfile.from_dict({
        "tax_year": 2024,
        "region": "Madrid",
        "filing_mode": "individual",
        "personal": {},
        "family": {"children_count": 1, "ascendants_count": 0},
        "income": {"work_gross": 30000.0, "work_net": 27500.0},
        "expenses": {},
        "documents": ["Libro de familia o certificado de convivencia"],
    })
    statuses = [evaluate_deduction(d, synth).status for d in deductions]
    applies = sum(1 for s in statuses if s == "applies")
    missing = sum(1 for s in statuses if s in {"missing_data", "missing_evidence"})
    pending = sum(1 for s in statuses if s == "pending_validation")
    assert applies >= 3, f"applies={applies}; estados={statuses}"
    assert missing >= 2, f"missing_*={missing}; estados={statuses}"
    assert pending == 0, f"pending_validation={pending}; estados={statuses}"


def _rich_profile() -> TaxProfile:
    """Perfil sintético deliberadamente generoso: ejerce las ramas calculables
    nuevas (descendientes tramificados, ascendientes >75, discapacidad severa
    con asistencia, maternidad, familia numerosa general, tributación conjunta
    biparental, pensiones, gastos del trabajo)."""
    return TaxProfile.from_dict({
        "tax_year": 2024,
        "region": "Madrid",
        "filing_mode": "conjunta",
        "personal": {
            "has_disability": True,
            "disability_degree": 65,
            "needs_assistance": True,
            "gender": "F",
        },
        "family": {
            "children_count": 4,
            "children_young_count": 1,
            "ascendants_count": 1,
            "ascendants_over_75_count": 1,
            "numerous_family_category": "general",
            "unit_type": "biparental",
        },
        "income": {"work_gross": 30000.0, "work_net": 27500.0},
        "expenses": {"pension_plan_individual": 2000.0},
        "documents": [
            "Libro de familia o certificado de convivencia",
            "Libro de familia",
            "Justificante de alta en SS o prestación",
            "Justificante de edad del ascendiente",
            "Certificado de convivencia con el ascendiente",
            "Certificado de discapacidad vigente",
            "Certificado de discapacidad vigente con grado ≥65%",
            "Certificado de discapacidad con mención de asistencia/movilidad reducida",
            "Certificado de la entidad gestora del plan",
            "Título vigente de familia numerosa",
        ],
    })


def test_qw1_rich_profile_yields_at_least_10_amounts_above_zero():
    """Criterio de aceptación QW1: tras tramificar manual_review en
    fixed_amount, un perfil sintético generoso debe producir al menos 10
    deducciones con importe estimado > 0 €."""
    deductions = load_deductions()
    results = [evaluate_deduction(d, _rich_profile()) for d in deductions]
    with_amount = [r for r in results if r.estimated_amount > 0]
    names = [
        (r.deduction_id, r.estimated_amount, r.status)
        for r in with_amount
    ]
    assert len(with_amount) >= 10, (
        f"Solo {len(with_amount)} deducciones con importe > 0; esperaba ≥10. "
        f"Detalle: {names}"
    )


def test_qw1_descendientes_tramificacion_cumulativa():
    """Con N hijos a cargo, la suma de los tramos 1..min(N,4) debe coincidir
    con el cuadro AEAT: 2.400 / 5.100 / 9.100 / 13.600 €."""
    deductions = load_deductions()
    descendientes = [d for d in deductions if d.id.startswith("es_minimo_descendientes_tramo_")]
    assert len(descendientes) == 4, f"Esperaba 4 tramos de descendientes, hay {len(descendientes)}"

    expected_total = {1: 2400.0, 2: 5100.0, 3: 9100.0, 4: 13600.0}
    for n_children, expected in expected_total.items():
        profile_n = TaxProfile.from_dict({
            "tax_year": 2024,
            "region": "Madrid",
            "family": {"children_count": n_children},
            "documents": ["Libro de familia o certificado de convivencia"],
        })
        total = sum(
            evaluate_deduction(d, profile_n).estimated_amount
            for d in descendientes
        )
        assert total == expected, (
            f"Con {n_children} hijos esperaba {expected} €, salió {total} €"
        )


def test_qw1_familia_numerosa_general_y_especial_son_excluyentes():
    """Solo una de las dos deducciones de familia numerosa puede aplicar a la
    vez. Y los importes oficiales son 1.200 € / 2.400 €."""
    deductions = load_deductions()
    fn_general = next(d for d in deductions if d.id == "es_deduccion_familia_numerosa_general_2024")
    fn_especial = next(d for d in deductions if d.id == "es_deduccion_familia_numerosa_especial_2024")
    assert fn_general.calculation.fixed_amount == 1200.0
    assert fn_especial.calculation.fixed_amount == 2400.0

    base = {
        "tax_year": 2024,
        "region": "Madrid",
        "family": {},
        "documents": ["Título vigente de familia numerosa", "Título vigente de familia numerosa especial"],
    }
    p_general = TaxProfile.from_dict({**base, "family": {"numerous_family_category": "general"}})
    p_especial = TaxProfile.from_dict({**base, "family": {"numerous_family_category": "especial"}})
    assert evaluate_deduction(fn_general, p_general).estimated_amount == 1200.0
    assert evaluate_deduction(fn_especial, p_general).estimated_amount == 0.0
    assert evaluate_deduction(fn_general, p_especial).estimated_amount == 0.0
    assert evaluate_deduction(fn_especial, p_especial).estimated_amount == 2400.0


def test_qw1_legacy_ids_replaced_by_tramos():
    """Las ids monolíticas en manual_review (descendientes, ascendientes,
    discapacidad, maternidad, familia numerosa, tributación conjunta,
    reducción rendimientos trabajo) ya no existen en el corpus: se han
    sustituido por entradas tramificadas calculables."""
    ids = {d.id for d in load_deductions()}
    legacy_removed = {
        "es_minimo_descendientes_2024",
        "es_minimo_ascendientes_2024",
        "es_minimo_discapacidad_contribuyente_2024",
        "es_deduccion_maternidad_2024",
        "es_deduccion_familia_numerosa_2024",
        "es_reduccion_tributacion_conjunta_2024",
        "es_reduccion_rendimientos_trabajo_2024",
    }
    leftover = legacy_removed & ids
    assert not leftover, f"Quedan ids monolíticos sin tramificar: {sorted(leftover)}"


def test_validated_deduction_applies_with_evidence_and_caps_amount():
    result = evaluate_deduction(validated_deduction(), profile())
    assert result.status == "applies"
    assert result.estimated_amount == 100.0
    assert result.risk_level == "low"


def test_validated_deduction_detects_missing_data():
    result = evaluate_deduction(validated_deduction(), profile(expenses={}))
    assert result.status == "missing_data"
    assert result.missing_fields == ("expenses.test_amount",)


def test_validated_deduction_detects_missing_evidence():
    result = evaluate_deduction(validated_deduction(), profile(documents=[]))
    assert result.status == "missing_evidence"
    assert result.missing_documents == ("Justificante de prueba",)


def test_validated_deduction_does_not_apply_when_requirement_fails():
    result = evaluate_deduction(validated_deduction(), profile(expenses={"test_amount": 0.0}))
    assert result.status == "does_not_apply"


def test_deduction_for_other_tax_year_does_not_apply():
    result = evaluate_deduction(validated_deduction(tax_year=2024), profile())
    assert result.status == "does_not_apply"


def test_deduction_for_other_region_does_not_apply():
    result = evaluate_deduction(validated_deduction(scope="autonomico", region="Cataluña"), profile(region="Madrid"))
    assert result.status == "does_not_apply"


