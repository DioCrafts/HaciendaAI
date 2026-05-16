"""Modelos públicos del núcleo fiscal.

Reexporta los tipos para conservar los imports existentes:

    from hacienda_ai.models import Deduction, TaxProfile, ValidationError
"""

from ._common import (
    ValidationError,
    is_regional_bulletin_id,
    is_state_bulletin_id,
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
    Tier,
    ValidationStatus,
)
from .sentencia import (
    FalloSentido,
    Organo,
    RatioConfidence,
    Sentencia,
)

__all__ = [
    "Calculation",
    "Deduction",
    "DeductionCategory",
    "FalloSentido",
    "ForalTerritory",
    "Norma",
    "NormaRegistry",
    "NormaStatus",
    "Organo",
    "RatioConfidence",
    "Requirement",
    "RiskLevel",
    "RiskLiteral",
    "RuleEvaluation",
    "RuleStatus",
    "Scope",
    "Sentencia",
    "Source",
    "SourceKind",
    "TaxProfile",
    "Tier",
    "ValidationError",
    "ValidationStatus",
    "VersionNorma",
    "is_regional_bulletin_id",
    "is_state_bulletin_id",
    "parse_iso_date",
    "require_iso_date",
    "validate_content_hash",
]
