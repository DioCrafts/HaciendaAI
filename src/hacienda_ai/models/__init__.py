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
from .consulta_dgt import (
    ConsultaDGT,
    CriterioConfidence,
    Impuesto,
)
from .norma import (
    Norma,
    NormaRegistry,
    NormaStatus,
    SourceKind,
    VersionNorma,
)
from .resolucion_teac import (
    OrganoTEA,
    ResolucionTEAC,
    SentidoResolucion,
    TipoResolucion,
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
    "ConsultaDGT",
    "CriterioConfidence",
    "Deduction",
    "DeductionCategory",
    "FalloSentido",
    "ForalTerritory",
    "Impuesto",
    "Norma",
    "NormaRegistry",
    "NormaStatus",
    "Organo",
    "OrganoTEA",
    "RatioConfidence",
    "Requirement",
    "ResolucionTEAC",
    "RiskLevel",
    "RiskLiteral",
    "RuleEvaluation",
    "RuleStatus",
    "Scope",
    "Sentencia",
    "SentidoResolucion",
    "Source",
    "SourceKind",
    "TaxProfile",
    "TipoResolucion",
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
