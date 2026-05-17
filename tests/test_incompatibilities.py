"""Golden cases para `resolve_incompatibilities`.

Cubre el agregador post-evaluación que detecta deducciones mutuamente
excluyentes que aplicarían a la vez y las degrada a
`requires_user_choice`. Sin este paso, el motor recomendaría aplicar
ambas y Hacienda regularizaría: es el error de mayor coste real que
puede cometer un asesor.

Filosofía de los tests:

- Trabajamos con deducciones sintéticas (`_make`) para aislar la lógica
  del agregador del catálogo real. Un cambio en `data/deductions/*.json`
  no debería tumbar estos tests; los del catálogo viven en
  `test_deductions.py`.
- Verificamos no solo el `status` sino el `estimated_amount` (debe
  quedar a 0 en `requires_user_choice`, para que el asesor no las sume
  por error) y el `reason` (debe enumerar las alternativas).
"""

from __future__ import annotations

from hacienda_ai.models import Deduction, TaxProfile
from hacienda_ai.rules import (
    evaluate_deduction,
    evaluate_deductions,
    resolve_incompatibilities,
)


def _make(id_: str, *, incompatibilities: tuple[str, ...] = ()) -> Deduction:
    """Deducción sintética validada con cálculo fijo == 100 €.

    El importe fijo nos permite identificar al instante qué alternativa
    aparece en el `reason` (todas devuelven 100; si el agregador rompiera
    el cálculo, sería evidente).
    """
    return Deduction.from_dict(
        {
            "id": id_,
            "name": f"Test {id_}",
            "description": "Deducción sintética para test del agregador.",
            "tax_year": 2025,
            "scope": "estatal",
            "region": None,
            "category": "deduccion",
            "requirements": [
                {"field": "expenses.flag", "operator": ">", "value": 0},
            ],
            "calculation": {"type": "fixed_amount", "fixed_amount": 100.0},
            "limit": None,
            "taxable_base_limits": {},
            "incompatibilities": list(incompatibilities),
            "required_documents": [],
            "rent_web_boxes": [],
            "sources": [
                {
                    "kind": "ley",
                    "title": "LIRPF (sintético)",
                    "url": "https://www.boe.es/buscar/act.php?id=BOE-A-2006-20764",
                    "article": "art. 1 (test)",
                    "paragraph": None,
                    "boe_id": "BOE-A-2006-20764",
                    "content_hash": "a" * 64,
                    "checked_at": "2026-05-17",
                }
            ],
            "effective_from": "2025-01-01",
            "effective_to": "2025-12-31",
            "last_reviewed_at": "2026-05-17",
            "risk_level": "bajo",
            "validation_status": "validada",
        }
    )


def _profile(flag: float = 1.0) -> TaxProfile:
    """Perfil mínimo que dispara `applies` en todas las `_make`."""
    return TaxProfile.from_dict(
        {
            "tax_year": 2025,
            "region": "Madrid",
            "expenses": {"flag": flag},
            "documents": [],
        }
    )


# ---------------------------------------------------------------------------
# Caso 1 — Sin conflicto declarado, todo `applies` permanece intacto.
# ---------------------------------------------------------------------------
def test_no_incompatibilities_keeps_applies():
    a = _make("a")
    b = _make("b")
    result = evaluate_deductions([a, b], _profile())
    statuses = {e.deduction_id: e.status for e in result}
    assert statuses == {"a": "applies", "b": "applies"}
    amounts = {e.deduction_id: e.estimated_amount for e in result}
    assert amounts == {"a": 100.0, "b": 100.0}


# ---------------------------------------------------------------------------
# Caso 2 — Par mutuamente excluyente declarado en ambos sentidos.
# ---------------------------------------------------------------------------
def test_mutual_conflict_degrades_both_to_user_choice():
    a = _make("a", incompatibilities=("b",))
    b = _make("b", incompatibilities=("a",))
    result = evaluate_deductions([a, b], _profile())
    statuses = {e.deduction_id: e.status for e in result}
    assert statuses == {
        "a": "requires_user_choice",
        "b": "requires_user_choice",
    }
    # El importe se pone a 0 explícitamente para que el asesor no sume
    # ambas — esa es la regla anti-error de coste real.
    assert all(e.estimated_amount == 0.0 for e in result)


# ---------------------------------------------------------------------------
# Caso 3 — Reciprocidad: solo uno declara la incompatibilidad, el otro
# no. El agregador la hace simétrica.
# ---------------------------------------------------------------------------
def test_asymmetric_declaration_becomes_symmetric():
    a = _make("a", incompatibilities=("b",))
    b = _make("b")  # no declara nada
    result = evaluate_deductions([a, b], _profile())
    statuses = {e.deduction_id: e.status for e in result}
    assert statuses == {
        "a": "requires_user_choice",
        "b": "requires_user_choice",
    }


# ---------------------------------------------------------------------------
# Caso 4 — Cierre transitivo: A↔B y B↔C deja A y C en el mismo
# componente, aunque ninguna se cite directamente.
# ---------------------------------------------------------------------------
def test_transitive_chain_groups_all_into_one_component():
    a = _make("a", incompatibilities=("b",))
    b = _make("b", incompatibilities=("a", "c"))
    c = _make("c", incompatibilities=("b",))
    result = evaluate_deductions([a, b, c], _profile())
    statuses = {e.deduction_id: e.status for e in result}
    assert statuses == {
        "a": "requires_user_choice",
        "b": "requires_user_choice",
        "c": "requires_user_choice",
    }
    # El reason de A debe mencionar a C aunque A no la cite directamente
    # — eso confirma el cierre transitivo.
    a_reason = next(e for e in result if e.deduction_id == "a").reason
    assert "(c)" in a_reason and "(b)" in a_reason


# ---------------------------------------------------------------------------
# Caso 5 — Si una de las dos no `applies`, la otra queda intacta. El
# agregador solo opera sobre conflictos REALES (los dos lados aplican).
# ---------------------------------------------------------------------------
def test_only_one_applies_keeps_it_unchanged():
    a = _make("a", incompatibilities=("b",))
    b = _make("b", incompatibilities=("a",))
    # b no aplica porque exige flag > 0; profile con flag=0 → b devuelve
    # `does_not_apply`. Para que `a` siga aplicando, hace falta evaluar
    # cada una con su propio perfil, pero como ambas comparten `flag`,
    # forzamos una con cálculo distinto: dejamos a `b` sin flag positivo
    # creando un perfil custom.
    pa = TaxProfile.from_dict(
        {"tax_year": 2025, "region": "Madrid", "expenses": {"flag": 1.0}, "documents": []}
    )
    pb = TaxProfile.from_dict(
        {"tax_year": 2025, "region": "Madrid", "expenses": {"flag": 0.0}, "documents": []}
    )
    eval_a = evaluate_deduction(a, pa)
    eval_b = evaluate_deduction(b, pb)
    resolved = resolve_incompatibilities([eval_a, eval_b], [a, b])
    statuses = {e.deduction_id: e.status for e in resolved}
    assert statuses["a"] == "applies"
    # `b` no aplica por sus requisitos, no por el agregador.
    assert statuses["b"] == "does_not_apply"


# ---------------------------------------------------------------------------
# Caso 6 — Incompatibilidad declarada hacia un ID que no está en el
# corpus evaluado: se ignora silenciosamente (no crashea, no degrada).
# ---------------------------------------------------------------------------
def test_dangling_incompatibility_id_is_ignored():
    # `a` cita `ghost` que no se evalúa nunca → `a` debe quedar applies.
    a = _make("a", incompatibilities=("ghost",))
    result = evaluate_deductions([a], _profile())
    assert len(result) == 1
    assert result[0].status == "applies"
    assert result[0].estimated_amount == 100.0


# ---------------------------------------------------------------------------
# Caso 7 — El reason debe enumerar las alternativas con sus importes,
# para que el asesor pueda elegir con criterio (no oculta la
# información). Sin esto el campo `requires_user_choice` sería un
# callejón sin salida.
# ---------------------------------------------------------------------------
def test_reason_lists_alternatives_with_amounts():
    a = _make("a", incompatibilities=("b",))
    b = _make("b", incompatibilities=("a",))
    result = evaluate_deductions([a, b], _profile())
    a_reason = next(e for e in result if e.deduction_id == "a").reason
    assert "Test b" in a_reason  # nombre legible del rival
    assert "100.00" in a_reason  # importe del rival
    assert "(b)" in a_reason  # id explícito
    # El propio importe que la deducción habría tenido también aparece,
    # para que el asesor compare directamente.
    assert "Importe propio si se eligiera esta: 100.00" in a_reason


# ---------------------------------------------------------------------------
# Caso 8 — Conflicto parcial dentro de un grupo de 3: solo dos
# aplican, la tercera está en `missing_data`. El agregador solo opera
# sobre las dos que aplican; la tercera no se toca.
# ---------------------------------------------------------------------------
def test_partial_conflict_only_marks_applying_pair():
    a = _make("a", incompatibilities=("b", "c"))
    b = _make("b", incompatibilities=("a", "c"))
    c = _make("c", incompatibilities=("a", "b"))
    # Perfil donde solo `flag` está; a, b, c usan el mismo campo así que
    # todas aplicarían. Forzamos `c` a `missing_data` con un requisito
    # extra que pide un campo inexistente.
    c_strict = Deduction.from_dict(
        {
            **{
                k: getattr(c, k) if k != "requirements" else None
                for k in (
                    "id",
                    "name",
                    "description",
                    "tax_year",
                    "scope",
                    "region",
                    "category",
                    "limit",
                    "taxable_base_limits",
                    "required_documents",
                    "rent_web_boxes",
                    "risk_level",
                )
            },
            "id": "c",
            "scope": "estatal",
            "region": None,
            "category": "deduccion",
            "tax_year": 2025,
            "requirements": [
                {"field": "expenses.flag", "operator": ">", "value": 0},
                {"field": "personal.missing_field", "operator": "exists"},
            ],
            "calculation": {"type": "fixed_amount", "fixed_amount": 100.0},
            "incompatibilities": ["a", "b"],
            "limit": None,
            "taxable_base_limits": {},
            "required_documents": [],
            "rent_web_boxes": [],
            "sources": [
                {
                    "kind": "ley",
                    "title": "LIRPF (sintético)",
                    "url": "https://www.boe.es/buscar/act.php?id=BOE-A-2006-20764",
                    "article": "art. 1 (test)",
                    "paragraph": None,
                    "boe_id": "BOE-A-2006-20764",
                    "content_hash": "a" * 64,
                    "checked_at": "2026-05-17",
                }
            ],
            "effective_from": "2025-01-01",
            "effective_to": "2025-12-31",
            "last_reviewed_at": "2026-05-17",
            "risk_level": "bajo",
            "validation_status": "validada",
        }
    )
    result = evaluate_deductions([a, b, c_strict], _profile())
    statuses = {e.deduction_id: e.status for e in result}
    assert statuses["a"] == "requires_user_choice"
    assert statuses["b"] == "requires_user_choice"
    assert statuses["c"] == "missing_data"


# ---------------------------------------------------------------------------
# Caso 9 — Garantía de que `resolve_incompatibilities` es idempotente:
# llamarla dos veces sobre el mismo input devuelve el mismo output
# (importante porque el orquestador podría reaplicarla por seguridad
# tras filtros adicionales).
# ---------------------------------------------------------------------------
def test_resolve_is_idempotent():
    a = _make("a", incompatibilities=("b",))
    b = _make("b", incompatibilities=("a",))
    initial = evaluate_deductions([a, b], _profile())
    second = resolve_incompatibilities(initial, [a, b])
    third = resolve_incompatibilities(second, [a, b])
    assert [e.status for e in second] == [e.status for e in third]
    assert [e.estimated_amount for e in second] == [e.estimated_amount for e in third]
    assert [e.reason for e in second] == [e.reason for e in third]


# ---------------------------------------------------------------------------
# Caso 10 — Caso real del catálogo: las dos deducciones por
# régimen transitorio de vivienda habitual (alquiler DT 15ª vs.
# inversión DT 18ª) son mutuamente excluyentes. Si un perfil
# improbable cumpliera ambas (tener contrato de alquiler pre-2015 Y
# haber adquirido vivienda pre-2013), el agregador debe degradarlas
# para que el asesor elija.
# ---------------------------------------------------------------------------
def test_real_catalog_vivienda_habitual_transitoria_conflict():
    from hacienda_ai.deductions import load_deductions

    catalog = load_deductions()
    alquiler = next(
        d for d in catalog if d.id == "es_deduccion_alquiler_vivienda_habitual_transitoria_2024"
    )
    inversion = next(
        d for d in catalog if d.id == "es_deduccion_inversion_vivienda_habitual_transitoria_2024"
    )
    # La declaración cruzada debe estar en ambas direcciones tras el
    # commit de QW3 (la simetría también la garantiza el agregador, pero
    # comprobar el JSON evita regresiones silenciosas).
    assert inversion.id in alquiler.incompatibilities
    assert alquiler.id in inversion.incompatibilities

    profile = TaxProfile.from_dict(
        {
            "tax_year": 2024,
            "region": "Madrid",
            "personal": {
                "rent_contract_before_2015": True,
                "dwelling_acquired_before_2013": True,
            },
            "expenses": {
                "rent_habitual_dwelling": 6000.0,
                "investment_habitual_dwelling": 5000.0,
            },
            "documents": [
                "Contrato de arrendamiento anterior a 2015",
                "Recibos de alquiler",
                "Escritura de adquisición anterior a 2013",
                "Justificantes de las cantidades satisfechas en el ejercicio",
            ],
        }
    )
    result = evaluate_deductions([alquiler, inversion], profile)
    statuses = {e.deduction_id: e.status for e in result}
    # `alquiler` está como `requires_manual_calculation` (cálculo no
    # lineal); por tanto NO está en `applies` y el agregador no debería
    # marcarla como `requires_user_choice`. La inversión sí queda
    # `applies` y al ser la única del grupo, permanece intacta.
    assert statuses[alquiler.id] == "requires_manual_calculation"
    assert statuses[inversion.id] == "applies"
    # Si ambas estuvieran en `applies`, el agregador degradaría las
    # dos. Verificamos esa rama directamente forzando el escenario.
    eval_alquiler_synth = next(e for e in result if e.deduction_id == alquiler.id)
    forced_applies = type(eval_alquiler_synth)(
        deduction_id=eval_alquiler_synth.deduction_id,
        status="applies",
        estimated_amount=600.0,
        reason="forzado",
        missing_fields=eval_alquiler_synth.missing_fields,
        missing_documents=eval_alquiler_synth.missing_documents,
        sources=eval_alquiler_synth.sources,
        risk_level=eval_alquiler_synth.risk_level,
        confidence=eval_alquiler_synth.confidence,
    )
    eval_inversion = next(e for e in result if e.deduction_id == inversion.id)
    resolved = resolve_incompatibilities(
        [forced_applies, eval_inversion], [alquiler, inversion]
    )
    statuses2 = {e.deduction_id: e.status for e in resolved}
    assert statuses2[alquiler.id] == "requires_user_choice"
    assert statuses2[inversion.id] == "requires_user_choice"
