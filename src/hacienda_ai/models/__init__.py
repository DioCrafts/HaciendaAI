"""Modelos públicos del núcleo fiscal.

Reexporta los tipos para conservar los imports existentes:

    from hacienda_ai.models import Deduction, TaxProfile, ValidationError
"""

from ._common import (
    ValidationError,
    parse_iso_date,
    require_iso_date,
    validate_content_hash,
)
from .norma import (
    Norma,
    NormaRegistry,
    NormaStatus,
    SourceKind,
    VersionNorma,
)
from .schema import (
    Calculation,
    Deduction,
    DeductionCategory,
    ForalTerritory,
    Requirement,
    RiskLevel,
    RiskLiteral,
    RuleEvaluation,
    RuleStatus,
    Scope,
    Source,
    TaxProfile,
    ValidationStatus,
)

__all__ = [
    "Calculation",
    "Deduction",
    "DeductionCategory",
    "ForalTerritory",
    "Norma",
    "NormaRegistry",
    "NormaStatus",
    "Requirement",
    "RiskLevel",
    "RiskLiteral",
    "RuleEvaluation",
    "RuleStatus",
    "Scope",
    "Source",
    "SourceKind",
    "TaxProfile",
    "ValidationError",
    "ValidationStatus",
    "VersionNorma",
    "parse_iso_date",
    "require_iso_date",
    "validate_content_hash",
]
