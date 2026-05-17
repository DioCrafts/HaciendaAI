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
from .article_version import (
    ArticleRegistry,
    VersionArticulo,
)
from .consulta_dgt import (
    ConsultaDGT,
    CriterioConfidence,
    Impuesto,
)
from .manual_aeat import (
    ManualChunk,
    ManualFuente,
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
    "ArticleRegistry",
    "Calculation",
    "ConsultaDGT",
    "CriterioConfidence",
    "Deduction",
    "DeductionCategory",
    "FalloSentido",
    "ForalTerritory",
    "Impuesto",
    "ManualChunk",
    "ManualFuente",
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
    "VersionArticulo",
    "VersionNorma",
    "is_regional_bulletin_id",
    "is_state_bulletin_id",
    "parse_iso_date",
    "require_iso_date",
    "validate_content_hash",
]
