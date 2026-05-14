"""Tests fiscales del lote 3: maternidad, familia numerosa y discapacidad.

Como en los lotes anteriores, las reglas viven en el JSON como
'pendiente_tests' y se flipean a VALIDADA con dataclasses.replace
para verificar el motor. El JSON no se modifica.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from hacienda_ai.deductions import load_deductions
from hacienda_ai.models import Deduction, TaxProfile, ValidationStatus
from hacienda_ai.rules import evaluate_deduction, evaluate_deductions


def _load_validated(deduction_id: str) -> Deduction:
    deductions = {d.id: d for d in load_deductions()}
    return replace(deductions[deduction_id], validation_status=ValidationStatus.VALIDADA)


def _profile(**overrides: Any) -> TaxProfile:
    data: dict[str, Any] = {
        "tax_year": 2025,
        "region": "Madrid",
        "income": {"work_income": 25000.0},
        "expenses": {},
        "documents": [],
    }
    data.update(overrides)
    return TaxProfile.from_dict(data)


# ---------- maternidad ----------


def test_maternity_applies_full_year_for_one_qualifying_child() -> None:
    deduction = _load_validated("es_maternidad_2025")
    profile = _profile(
        personal={"is_eligible_maternity_deduction": True},
        family={"maternity_qualifying_child_months": 12},
        documents=[
            "Libro de familia o certificación equivalente",
            "Vida laboral o documento que acredite la situación laboral",
        ],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "applies"
    assert result.estimated_amount == 1200.0


def test_maternity_prorates_by_qualifying_months() -> None:
    deduction = _load_validated("es_maternidad_2025")
    profile = _profile(
        personal={"is_eligible_maternity_deduction": True},
        family={"maternity_qualifying_child_months": 7},
        documents=[
            "Libro de familia o certificación equivalente",
            "Vida laboral o documento que acredite la situación laboral",
        ],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.estimated_amount == 700.0


def test_maternity_scales_with_two_qualifying_children() -> None:
    deduction = _load_validated("es_maternidad_2025")
    profile = _profile(
        personal={"is_eligible_maternity_deduction": True},
        family={"maternity_qualifying_child_months": 24},
        documents=[
            "Libro de familia o certificación equivalente",
            "Vida laboral o documento que acredite la situación laboral",
        ],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.estimated_amount == 2400.0


def test_maternity_does_not_apply_without_eligibility_flag() -> None:
    deduction = _load_validated("es_maternidad_2025")
    profile = _profile(
        personal={"is_eligible_maternity_deduction": False},
        family={"maternity_qualifying_child_months": 12},
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "does_not_apply"


# ---------- familia numerosa ----------


def test_large_family_general_full_year_yields_1200() -> None:
    deduction = _load_validated("es_familia_numerosa_general_2025")
    profile = _profile(
        personal={"large_family_category": "general"},
        family={"large_family_qualifying_months": 12},
        documents=["Título de familia numerosa en vigor"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "applies"
    assert result.estimated_amount == 1200.0


def test_large_family_general_prorates_by_months() -> None:
    deduction = _load_validated("es_familia_numerosa_general_2025")
    profile = _profile(
        personal={"large_family_category": "general"},
        family={"large_family_qualifying_months": 5},
        documents=["Título de familia numerosa en vigor"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.estimated_amount == 500.0


def test_large_family_general_caps_at_12_months() -> None:
    deduction = _load_validated("es_familia_numerosa_general_2025")
    profile = _profile(
        personal={"large_family_category": "general"},
        family={"large_family_qualifying_months": 20},  # error de captura, debe recortarse
        documents=["Título de familia numerosa en vigor"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.estimated_amount == 1200.0


def test_large_family_general_does_not_apply_to_especial_category() -> None:
    deduction = _load_validated("es_familia_numerosa_general_2025")
    profile = _profile(
        personal={"large_family_category": "especial"},
        family={"large_family_qualifying_months": 12},
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "does_not_apply"


def test_large_family_especial_full_year_yields_2400() -> None:
    deduction = _load_validated("es_familia_numerosa_especial_2025")
    profile = _profile(
        personal={"large_family_category": "especial"},
        family={"large_family_qualifying_months": 12},
        documents=["Título de familia numerosa en vigor"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.estimated_amount == 2400.0


def test_large_family_especial_wins_over_general_when_both_evaluable() -> None:
    """Solo una categoría puede aplicar a la vez por requirements, pero
    si por error de datos ambos se evaluaran, la incompatibilidad debe
    descartar la general."""
    general = _load_validated("es_familia_numerosa_general_2025")
    especial = _load_validated("es_familia_numerosa_especial_2025")
    # Profile que cumple SOLO especial; general debe quedar does_not_apply
    # por requirement no por incompatibilidad.
    profile = _profile(
        personal={"large_family_category": "especial"},
        family={"large_family_qualifying_months": 12},
        documents=["Título de familia numerosa en vigor"],
    )
    results = evaluate_deductions([general, especial], profile)
    by_id = {r.deduction_id: r for r in results}
    assert by_id["es_familia_numerosa_especial_2025"].status == "applies"
    assert by_id["es_familia_numerosa_general_2025"].status == "does_not_apply"


# ---------- discapacidad ----------


def test_disabled_descendant_full_year_one_descendant() -> None:
    deduction = _load_validated("es_descendiente_discapacidad_2025")
    profile = _profile(
        family={"disabled_descendants_qualifying_months": 12},
        documents=["Certificado de discapacidad del descendiente"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "applies"
    assert result.estimated_amount == 1200.0


def test_disabled_descendant_scales_with_multiple_descendants() -> None:
    deduction = _load_validated("es_descendiente_discapacidad_2025")
    profile = _profile(
        family={"disabled_descendants_qualifying_months": 36},  # 3 descendientes 12 meses
        documents=["Certificado de discapacidad del descendiente"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.estimated_amount == 3600.0


def test_disabled_ascendant_prorates_by_months() -> None:
    deduction = _load_validated("es_ascendiente_discapacidad_2025")
    profile = _profile(
        family={"disabled_ascendants_qualifying_months": 8},
        documents=["Certificado de discapacidad del ascendiente"],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.estimated_amount == 800.0


def test_disabled_ascendant_missing_evidence() -> None:
    deduction = _load_validated("es_ascendiente_discapacidad_2025")
    profile = _profile(
        family={"disabled_ascendants_qualifying_months": 12},
        documents=[],
    )
    result = evaluate_deduction(deduction, profile)
    assert result.status == "missing_evidence"
    assert result.estimated_amount == 1200.0
    assert result.missing_documents == ("Certificado de discapacidad del ascendiente",)
