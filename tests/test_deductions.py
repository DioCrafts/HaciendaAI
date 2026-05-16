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
    """El corpus semilla carga ≥30 deducciones, todas validadas, con anclaje
    apropiado al tipo de boletín: BOE-A-... + content_hash para estatales,
    y BOCM-/DOGC-/... (sin hash) para autonómicas, según la regla relajada
    de QW4."""
    deductions = load_deductions()
    assert len(deductions) >= 30, f"Se esperaban ≥30 deducciones, hay {len(deductions)}"
    assert all(d.validation_status.value == "validada" for d in deductions), (
        "Hay deducciones no validadas en el corpus semilla"
    )
    for d in deductions:
        if d.scope.value == "estatal":
            anchors = [
                s for s in d.sources
                if s.boe_id and s.boe_id.startswith("BOE-A-") and s.content_hash
            ]
            assert anchors, (
                f"Deducción estatal {d.id} sin fuente BOE-anclada con content_hash"
            )
        else:
            anchors = [s for s in d.sources if s.boe_id]
            assert anchors, (
                f"Deducción {d.scope.value} {d.id} sin boe_id en ninguna fuente"
            )
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
    descendientes = [
        d for d in deductions
        if d.id.startswith("es_minimo_descendientes_tramo_") and d.tax_year == 2024
    ]
    assert len(descendientes) == 4, f"Esperaba 4 tramos de descendientes (2024), hay {len(descendientes)}"

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


def _rich_madrid_profile() -> TaxProfile:
    """Perfil sintético orientado a ejercer las deducciones autonómicas Madrid
    introducidas en QW4 además de las estatales."""
    return TaxProfile.from_dict({
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
        "income": {"work_gross": 30000.0, "work_net": 27500.0},
        "expenses": {
            "rental_madrid_youth": 6000.0,
            "investment_madrid_startups": 5000.0,
            "donations_madrid_cultural": 200.0,
            "cultural_consumption_madrid": 800.0,
        },
        "documents": [
            "Libro de familia o certificado de convivencia",
            "Libro de familia o resolución de adopción",
            "Resolución de adopción internacional",
            "Certificado de la Consejería competente sobre el acogimiento",
            "Contrato de arrendamiento",
            "Justificantes de pago",
            "Acreditación del depósito de fianza en la Agencia de Vivienda Social CM",
            "Certificado de alta y permanencia en RETA",
            "Certificación de la sociedad sobre cumplimiento de requisitos",
            "Justificante de suscripción y desembolso",
            "Certificado de la entidad donataria inscrita en el registro autonómico",
            "Justificantes de compra emitidos por entidades culturales radicadas en Madrid",
        ],
    })


def test_qw4_corpus_includes_at_least_10_madrid_entries() -> None:
    """El corpus combinado estatal + autonómico Madrid debe traer al menos 10
    deducciones con scope=autonomico y region=Madrid."""
    deductions = load_deductions()
    madrid = [d for d in deductions if d.scope.value == "autonomico" and d.region == "Madrid"]
    assert len(madrid) >= 10, f"solo {len(madrid)} entradas Madrid"
    for d in madrid:
        anchor_kinds = {s.boe_id.split("-")[0] for s in d.sources if s.boe_id}
        assert anchor_kinds, f"{d.id}: ninguna fuente con boe_id"
        # Anclaje a boletín autonómico reconocido (BOCM) — sin exigir hash.
        assert any(prefix == "BOCM" for prefix in anchor_kinds), (
            f"{d.id}: esperaba al menos una fuente BOCM-..., vi {anchor_kinds}"
        )


def test_qw4_madrid_profile_yields_state_plus_regional_applies() -> None:
    """Un perfil sintético madrileño debe ver aplicar al menos 3 deducciones
    autonómicas Madrid además del bloque estatal habitual."""
    deductions = load_deductions()
    results = [evaluate_deduction(d, _rich_madrid_profile()) for d in deductions]
    by_scope = {"estatal": [], "autonomico_madrid": []}
    for d, r in zip(deductions, results, strict=True):
        if d.scope.value == "autonomico" and d.region == "Madrid":
            by_scope["autonomico_madrid"].append(r)
        elif d.scope.value == "estatal":
            by_scope["estatal"].append(r)
    estatal_applies = [r for r in by_scope["estatal"] if r.status == "applies"]
    madrid_applies = [r for r in by_scope["autonomico_madrid"] if r.status == "applies"]
    assert len(estatal_applies) >= 4, f"estatales applies={len(estatal_applies)}"
    assert len(madrid_applies) >= 3, (
        f"autonómicas Madrid applies={len(madrid_applies)}; "
        f"esperaba ≥3 para que el demo tenga sentido en Madrid"
    )


def test_qw4_non_madrid_profile_does_not_yield_madrid_regional() -> None:
    """Cambiar la región a una CCAA distinta debe excluir todas las autonómicas
    Madrid (does_not_apply), sin afectar a las estatales."""
    deductions = load_deductions()
    sevilla_profile = TaxProfile.from_dict({
        **_rich_madrid_profile().to_dict(),
        "region": "Sevilla",
    })
    results = [evaluate_deduction(d, sevilla_profile) for d in deductions]
    for d, r in zip(deductions, results, strict=True):
        if d.scope.value == "autonomico" and d.region == "Madrid":
            assert r.status == "does_not_apply", (
                f"{d.id} aplicó con region=Sevilla: status={r.status}"
            )


def test_qw4_validada_accepts_regional_bulletin_without_hash() -> None:
    """El schema relajado por QW4 acepta validation_status=validada cuando la
    única fuente cita un boletín autonómico reconocido (BOCM-, DOGC-, ...) sin
    content_hash. Esto solo aplica a fuentes regionales: para BOE estatal sigue
    siendo obligatorio el hash."""
    base = {
        "id": "test_regional_validada",
        "name": "Regional sin hash",
        "description": "Sintética para QW4.",
        "tax_year": 2024,
        "scope": "autonomico",
        "region": "Madrid",
        "category": "deduccion",
        "requirements": [],
        "calculation": {"type": "fixed_amount", "fixed_amount": 100.0},
        "limit": None,
        "taxable_base_limits": {},
        "incompatibilities": [],
        "required_documents": [],
        "rent_web_boxes": [],
        "sources": [
            {
                "kind": "ley",
                "title": "BOCM sin hash",
                "url": "https://gestiona.comunidad.madrid/legislacion/x",
                "article": "art. 1",
                "boe_id": "BOCM-2010-258",
                "content_hash": None,
                "checked_at": "2026-05-16",
            }
        ],
        "effective_from": "2024-01-01",
        "effective_to": "2024-12-31",
        "last_reviewed_at": "2026-05-16",
        "risk_level": "bajo",
        "validation_status": "validada",
    }
    # No debe lanzar.
    Deduction.from_dict(base)


def test_qw4_validada_rejects_state_anchor_without_hash() -> None:
    """Asimétricamente, una cita BOE estatal sin content_hash sigue siendo
    inválida — solo se relaja el requisito para boletines regionales."""
    base = {
        "id": "test_estatal_sin_hash",
        "name": "Estatal sin hash",
        "description": "Sintética para QW4.",
        "tax_year": 2024,
        "scope": "estatal",
        "region": None,
        "category": "deduccion",
        "requirements": [],
        "calculation": {"type": "fixed_amount", "fixed_amount": 100.0},
        "limit": None,
        "taxable_base_limits": {},
        "incompatibilities": [],
        "required_documents": [],
        "rent_web_boxes": [],
        "sources": [
            {
                "kind": "ley",
                "title": "BOE estatal sin hash",
                "article": "art. 1",
                "boe_id": "BOE-A-2006-20764",
                "content_hash": None,
            }
        ],
        "effective_from": "2024-01-01",
        "effective_to": "2024-12-31",
        "last_reviewed_at": "2026-05-16",
        "risk_level": "bajo",
        "validation_status": "validada",
    }
    with pytest.raises(ValidationError, match="BOE estatal|content_hash"):
        Deduction.from_dict(base)


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


# --------------------------------------------------------------------------
# QW6: estado propio `requires_manual_calculation` para `manual_review`.
# Antes salía como `applies` con `estimated_amount=0.0`, lo que en el demo
# se leía como "Aplica · 0,00 €" y un asesor lo interpretaba como
# "no hay deducción". El nuevo estado mantiene visible que la regla aplica
# pero el importe lo tiene que calcular un humano sobre la fuente citada.
# --------------------------------------------------------------------------


def _manual_review_deduction(**overrides: object) -> Deduction:
    """Variante sintética con `calculation.type=manual_review` lista para
    aplicar (requisitos cumplidos, documentos presentes). Los `overrides`
    se fusionan sobre los defaults para permitir sustituir requirements o
    required_documents sin colisión de kwargs."""
    defaults = {
        "id": "test_manual_review",
        "requirements": [],
        "calculation": {"type": "manual_review", "base_field": "expenses.test_amount"},
        "limit": None,
        "required_documents": [],
    }
    defaults.update(overrides)
    return validated_deduction(**defaults)


def test_qw6_manual_review_surfaces_as_requires_manual_calculation():
    """Requisitos cumplidos + sin docs pendientes + cálculo no lineal:
    debe salir como `requires_manual_calculation`, NO como `applies`."""
    result = evaluate_deduction(_manual_review_deduction(), profile())
    assert result.status == "requires_manual_calculation"
    assert result.estimated_amount == 0.0
    assert "fórmula no lineal" in result.reason or "asesor" in result.reason
    # Confianza media: la regla aplica pero el importe es desconocido.
    assert 0.5 <= result.confidence < 0.8


def test_qw6_manual_review_still_blocks_on_missing_data():
    """Si faltan datos estructurados para evaluar los requisitos, el motor
    debe priorizar `missing_data` sobre `requires_manual_calculation`: el
    asesor ni siquiera puede empezar el cálculo manual sin esos datos."""
    deduction = _manual_review_deduction(
        requirements=[{"field": "expenses.unknown_field", "operator": ">", "value": 0}],
    )
    result = evaluate_deduction(deduction, profile())
    assert result.status == "missing_data"


def test_qw6_manual_review_still_blocks_on_missing_evidence():
    """Si faltan justificantes documentales, gana `missing_evidence`. El
    asesor primero recoge los papeles, luego ya verá si toca cálculo manual."""
    deduction = _manual_review_deduction(
        required_documents=["Justificante imposible de tener"],
    )
    result = evaluate_deduction(deduction, profile())
    assert result.status == "missing_evidence"


def test_qw6_corpus_manual_review_never_surfaces_as_applies_zero():
    """Invariante global del corpus: ninguna evaluación devuelve `applies`
    con importe 0 €. Si alguien añade una deducción `manual_review` futura
    y olvida la rama de QW6, este test la caza. Validado contra los dos
    perfiles sintéticos del repo (_rich_profile + _rich_madrid_profile)."""
    for builder in (_rich_profile, _rich_madrid_profile):
        for d in load_deductions():
            r = evaluate_deduction(d, builder())
            if r.status == "applies":
                assert r.estimated_amount > 0, (
                    f"{d.id} sale como 'applies' con 0 €: "
                    "síntoma de manual_review oculto. "
                    "Debe migrar a `requires_manual_calculation`."
                )


def test_qw6_rule_status_literal_includes_requires_manual_calculation():
    """Type-level: el Literal `RuleStatus` debe incluir el nuevo valor.
    Si alguien lo borra del modelo, mypy --strict y este test lo cazan."""
    from typing import get_args

    from hacienda_ai.models import RuleStatus

    assert "requires_manual_calculation" in get_args(RuleStatus)


# --------------------------------------------------------------------------
# Sprint 1 #1: corpus IRPF 2025 estatal. Se incorpora como clon estructural
# del corpus 2024 con vigencia trasladada a 2025, validado contra el texto
# BOE consolidado vigente con `verify_seed.py` (ok=33 drift=0 sobre la
# revisión del 2026-05-16). Es decir: para los preceptos LIRPF citados, el
# texto consolidado no se ha modificado entre 2024 y 2025 y los importes
# literales siguen siendo aplicables.
# --------------------------------------------------------------------------


def test_corpus_2025_loads_with_32_validated_state_entries():
    """El corpus 2025 debe cargar el mismo número de entradas estatales
    que 2024 (32). Si alguien suma o elimina deducciones a uno solo de los
    dos ejercicios, este test lo detecta."""
    deductions = load_deductions()
    state_2025 = [
        d for d in deductions
        if d.tax_year == 2025 and d.scope.value == "estatal"
    ]
    state_2024 = [
        d for d in deductions
        if d.tax_year == 2024 and d.scope.value == "estatal"
    ]
    assert len(state_2025) == 32, (
        f"Esperaba 32 deducciones estatales 2025; hay {len(state_2025)}"
    )
    assert len(state_2025) == len(state_2024), (
        "El corpus 2025 debería tener el mismo número de entradas estatales "
        f"que 2024 ({len(state_2024)}); hay {len(state_2025)}"
    )
    assert all(d.validation_status.value == "validada" for d in state_2025), (
        "Toda entrada del corpus 2025 estatal debe quedar como `validada`"
    )


def test_corpus_2025_vigencia_window_is_calendar_year():
    """Todas las entradas 2025 deben declarar vigencia [2025-01-01, 2025-12-31]
    para que el filtro temporal del motor las descarte automáticamente para
    devengos fuera del ejercicio."""
    from datetime import date
    state_2025 = [
        d for d in load_deductions()
        if d.tax_year == 2025 and d.scope.value == "estatal"
    ]
    for d in state_2025:
        assert d.effective_from == date(2025, 1, 1), (
            f"{d.id}: effective_from debe ser 2025-01-01, es {d.effective_from}"
        )
        assert d.effective_to == date(2025, 12, 31), (
            f"{d.id}: effective_to debe ser 2025-12-31, es {d.effective_to}"
        )


def test_corpus_2025_id_namespace_does_not_collide_with_2024():
    """Los ids de 2025 acaban en `_2025` y los de 2024 en `_2024`. El loader
    rechazaría ids duplicados; este test garantiza que el split por sufijo
    está completo (ningún id se queda sin renombrar)."""
    deductions = load_deductions()
    ids_2024 = {d.id for d in deductions if d.tax_year == 2024 and d.scope.value == "estatal"}
    ids_2025 = {d.id for d in deductions if d.tax_year == 2025 and d.scope.value == "estatal"}
    assert ids_2024 and ids_2025
    assert ids_2024.isdisjoint(ids_2025)
    assert all(i.endswith("_2024") for i in ids_2024)
    assert all(i.endswith("_2025") for i in ids_2025)
    # Cada deducción 2024 debe tener su gemela 2025
    pairs_missing = {i.removesuffix("_2024") for i in ids_2024} - {
        i.removesuffix("_2025") for i in ids_2025
    }
    assert not pairs_missing, (
        f"Deducciones 2024 sin equivalente 2025: {sorted(pairs_missing)}"
    )


def test_corpus_2025_profile_yields_applicable_deductions():
    """Un perfil 2025 razonable evaluado contra el motor debe devolver al
    menos un puñado de deducciones `applies` con importe > 0. Si esto baja
    a cero, algo se ha roto en la propagación de vigencia o en el filtrado
    por `tax_year`."""
    deductions = load_deductions()
    p = TaxProfile.from_dict({
        "tax_year": 2025,
        "region": "Madrid",
        "family": {"children_count": 2},
        "income": {"work_gross": 30000, "work_net": 27500},
        "expenses": {},
        "documents": ["Libro de familia o certificado de convivencia"],
    })
    applies_with_amount = [
        evaluate_deduction(d, p) for d in deductions
        if d.tax_year == 2025 and d.scope.value == "estatal"
    ]
    applied = [r for r in applies_with_amount if r.status == "applies"]
    with_amount = [r for r in applied if r.estimated_amount > 0]
    # El perfil sintético (2 hijos, trabajo declarado) dispara el mínimo del
    # contribuyente, dos tramos del mínimo por descendientes y el gasto del
    # trabajo de 2.000 €. Esperar ≥4 con importe deja margen para que el
    # corpus crezca sin volver el test frágil pero sigue verificando que
    # la propagación de vigencia y filtrado por tax_year funcionan en 2025.
    assert len(applied) >= 4, (
        f"Perfil 2025 sintético: esperaba ≥4 deducciones `applies` "
        f"sobre estatales; hay {len(applied)}"
    )
    assert len(with_amount) >= 4, (
        f"Perfil 2025 sintético: esperaba ≥4 deducciones con importe > 0; "
        f"hay {len(with_amount)}"
    )


def test_corpus_2025_inherits_2024_hashes_implying_stable_boe_text():
    """Decisión documentada: el clon 2025 conserva los `content_hash` del
    2024. Si en algún momento un artículo LIRPF cambia entre ejercicios,
    el `verify_seed.py` los marcará como drift. Este test pinea el invariante
    actual: misma cita → mismo hash en ambos años."""
    deductions = load_deductions()
    state_2024 = {d.id: d for d in load_deductions() if d.tax_year == 2024 and d.scope.value == "estatal"}
    state_2025 = {d.id: d for d in deductions if d.tax_year == 2025 and d.scope.value == "estatal"}
    for id_2025, d_2025 in state_2025.items():
        twin_id = id_2025.replace("_2025", "_2024")
        d_2024 = state_2024.get(twin_id)
        assert d_2024 is not None, f"sin gemelo 2024 para {id_2025}"
        hashes_2024 = sorted(s.content_hash for s in d_2024.sources if s.content_hash)
        hashes_2025 = sorted(s.content_hash for s in d_2025.sources if s.content_hash)
        assert hashes_2024 == hashes_2025, (
            f"{id_2025}: content_hash divergente respecto a {twin_id}. "
            f"Si la divergencia es legítima (norma modificada), actualizar "
            f"este test; si no, refrescar el JSON con verify_seed.py --update."
        )


