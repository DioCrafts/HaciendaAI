"""Modelos tipados y validadores ligeros para datos fiscales.

El proyecto arranca sin dependencias externas para que la base fiscal pueda
validarse en cualquier entorno. Si más adelante se incorpora FastAPI, estos
modelos pueden migrarse a Pydantic manteniendo los mismos campos públicos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any, Literal


class ValidationError(ValueError):
    """Error de validación de datos fiscales o reglas."""


class Scope(StrEnum):
    ESTATAL = "estatal"
    AUTONOMICO = "autonomico"
    LOCAL = "local"


class DeductionCategory(StrEnum):
    DEDUCCION = "deduccion"
    REDUCCION = "reduccion"
    EXENCION = "exencion"
    GASTO_DEDUCIBLE = "gasto_deducible"
    MINIMO_PERSONAL_FAMILIAR = "minimo_personal_familiar"
    COMPENSACION = "compensacion"
    AJUSTE = "ajuste"


class RiskLevel(StrEnum):
    BAJO = "bajo"
    MEDIO = "medio"
    ALTO = "alto"


class ValidationStatus(StrEnum):
    VALIDADA = "validada"
    PENDIENTE_FUENTE = "pendiente_fuente"
    PENDIENTE_TESTS = "pendiente_tests"
    OBSOLETA = "obsoleta"
    DUDOSA = "dudosa"


RuleStatus = Literal[
    "applies",
    "does_not_apply",
    "missing_data",
    "missing_evidence",
    "pending_validation",
]


@dataclass(frozen=True)
class Source:
    type: str
    title: str
    url: str | None = None
    checked_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Source:
        require_keys(data, ["type", "title"], "source")
        return cls(
            type=as_non_empty_str(data["type"], "source.type"),
            title=as_non_empty_str(data["title"], "source.title"),
            url=as_optional_str(data.get("url"), "source.url"),
            checked_at=as_optional_str(data.get("checked_at"), "source.checked_at"),
        )


@dataclass(frozen=True)
class Requirement:
    field: str
    operator: str
    value: Any = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Requirement:
        require_keys(data, ["field", "operator"], "requirement")
        operator = as_non_empty_str(data["operator"], "requirement.operator")
        if operator not in {"==", "!=", ">", ">=", "<", "<=", "exists", "not_exists", "in"}:
            raise ValidationError(f"Operador de requisito no soportado: {operator}")
        return cls(
            field=as_non_empty_str(data["field"], "requirement.field"),
            operator=operator,
            value=data.get("value"),
        )


@dataclass(frozen=True)
class Tier:
    up_to: float | None
    percentage: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Tier:
        require_keys(data, ["percentage"], "tier")
        percentage = as_optional_number(data["percentage"], "tier.percentage")
        if percentage is None or not (0 <= percentage <= 1):
            raise ValidationError("tier.percentage debe ser un número entre 0 y 1")
        up_to = as_optional_number(data.get("up_to"), "tier.up_to")
        if up_to is not None and up_to <= 0:
            raise ValidationError("tier.up_to debe ser positivo o null")
        return cls(up_to=up_to, percentage=percentage)


@dataclass(frozen=True)
class Calculation:
    type: str
    base_field: str | None = None
    percentage: float | None = None
    cap: float | None = None
    fixed_amount: float | None = None
    tiers: tuple[Tier, ...] = ()
    monthly_amount: float | None = None
    months_field: str | None = None
    months_cap: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Calculation:
        require_keys(data, ["type"], "calculation")
        calculation_type = as_non_empty_str(data["type"], "calculation.type")
        if calculation_type not in {
            "manual_review",
            "amount_field",
            "percentage_with_cap",
            "fixed_amount",
            "tiered_percentage",
            "prorated_fixed_amount",
        }:
            raise ValidationError(f"Tipo de cálculo no soportado: {calculation_type}")
        percentage = as_optional_number(data.get("percentage"), "calculation.percentage")
        cap = as_optional_number(data.get("cap"), "calculation.cap")
        fixed_amount = as_optional_number(data.get("fixed_amount"), "calculation.fixed_amount")
        if calculation_type == "percentage_with_cap" and percentage is not None and not (0 <= percentage <= 1):
            raise ValidationError("calculation.percentage debe expresarse entre 0 y 1")
        tiers_raw = data.get("tiers")
        if calculation_type == "tiered_percentage":
            if not tiers_raw:
                raise ValidationError("calculation.tiers es obligatorio para type=tiered_percentage")
            tiers = tuple(Tier.from_dict(item) for item in as_list(tiers_raw, "calculation.tiers"))
            _validate_tier_thresholds(tiers)
        else:
            if tiers_raw:
                raise ValidationError("calculation.tiers solo se acepta con type=tiered_percentage")
            tiers = ()
        monthly_amount = as_optional_number(data.get("monthly_amount"), "calculation.monthly_amount")
        months_field = as_optional_str(data.get("months_field"), "calculation.months_field")
        months_cap = as_optional_number(data.get("months_cap"), "calculation.months_cap")
        if calculation_type == "prorated_fixed_amount":
            if monthly_amount is None or monthly_amount < 0:
                raise ValidationError("calculation.monthly_amount es obligatorio y >= 0 para prorated_fixed_amount")
            if not months_field:
                raise ValidationError("calculation.months_field es obligatorio para prorated_fixed_amount")
            if months_cap is not None and months_cap <= 0:
                raise ValidationError("calculation.months_cap debe ser positivo o null")
        else:
            for extra_field, value in (
                ("monthly_amount", monthly_amount),
                ("months_field", months_field),
                ("months_cap", months_cap),
            ):
                if value is not None:
                    raise ValidationError(f"calculation.{extra_field} solo se acepta con type=prorated_fixed_amount")
        return cls(
            type=calculation_type,
            base_field=as_optional_str(data.get("base_field"), "calculation.base_field"),
            percentage=percentage,
            cap=cap,
            fixed_amount=fixed_amount,
            tiers=tiers,
            monthly_amount=monthly_amount,
            months_field=months_field,
            months_cap=months_cap,
        )


def _validate_tier_thresholds(tiers: tuple[Tier, ...]) -> None:
    last_threshold = 0.0
    for index, tier in enumerate(tiers):
        if tier.up_to is None:
            if index != len(tiers) - 1:
                raise ValidationError("Solo el último tier puede tener up_to=null")
        else:
            if tier.up_to <= last_threshold:
                raise ValidationError("Los thresholds 'up_to' deben ser estrictamente crecientes")
            last_threshold = tier.up_to


TAXABLE_BASE_LIMIT_KEYS: frozenset[str] = frozenset(
    {
        "max_percentage_of_base_liquidable",
        "max_percentage_of_base_general",
        "max_percentage_of_base_savings",
    }
)


def _validate_taxable_base_limits(limits: dict[str, Any]) -> None:
    for key, value in limits.items():
        if key not in TAXABLE_BASE_LIMIT_KEYS:
            raise ValidationError(
                f"taxable_base_limits.{key} no es una clave reconocida. Permitidas: {sorted(TAXABLE_BASE_LIMIT_KEYS)}"
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not (0 <= value <= 1):
            raise ValidationError(f"taxable_base_limits.{key} debe ser un número entre 0 y 1")


def _parse_taxable_base_limits(raw: Any) -> dict[str, float]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValidationError("taxable_base_limits debe ser un objeto JSON")
    _validate_taxable_base_limits(raw)
    return {key: float(value) for key, value in raw.items()}


@dataclass(frozen=True)
class Deduction:
    id: str
    name: str
    description: str
    tax_year: int
    scope: Scope
    region: str | None
    category: DeductionCategory
    requirements: tuple[Requirement, ...]
    calculation: Calculation
    limit: float | None
    taxable_base_limits: dict[str, float]
    incompatibilities: tuple[str, ...]
    required_documents: tuple[str, ...]
    rent_web_boxes: tuple[str, ...]
    sources: tuple[Source, ...]
    effective_from: str | None
    effective_to: str | None
    last_reviewed_at: str | None
    risk_level: RiskLevel
    validation_status: ValidationStatus

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Deduction:
        require_keys(
            data,
            [
                "id",
                "name",
                "description",
                "tax_year",
                "scope",
                "category",
                "requirements",
                "calculation",
                "required_documents",
                "sources",
                "risk_level",
                "validation_status",
            ],
            "deduction",
        )
        sources = tuple(Source.from_dict(item) for item in as_list(data["sources"], "sources"))
        if not sources:
            raise ValidationError("Cada deducción debe incluir al menos una fuente o una marca pendiente de fuente")
        tax_year = as_tax_year(data["tax_year"], "tax_year")
        scope = Scope(data["scope"])
        region = as_optional_str(data.get("region"), "region")
        if scope == Scope.AUTONOMICO and not region:
            raise ValidationError("Las deducciones con scope='autonomico' deben especificar 'region'")
        effective_from = as_optional_iso_date(data.get("effective_from"), "effective_from")
        effective_to = as_optional_iso_date(data.get("effective_to"), "effective_to")
        if effective_from and effective_to and effective_from > effective_to:
            raise ValidationError("effective_from no puede ser posterior a effective_to")
        return cls(
            id=as_non_empty_str(data["id"], "id"),
            name=as_non_empty_str(data["name"], "name"),
            description=as_non_empty_str(data["description"], "description"),
            tax_year=tax_year,
            scope=scope,
            region=region,
            category=DeductionCategory(data["category"]),
            requirements=tuple(Requirement.from_dict(item) for item in as_list(data["requirements"], "requirements")),
            calculation=Calculation.from_dict(data["calculation"]),
            limit=as_optional_number(data.get("limit"), "limit"),
            taxable_base_limits=_parse_taxable_base_limits(data.get("taxable_base_limits")),
            incompatibilities=tuple(
                as_non_empty_str(item, "incompatibility") for item in (data.get("incompatibilities") or [])
            ),
            required_documents=tuple(
                as_non_empty_str(item, "required_document") for item in (data.get("required_documents") or [])
            ),
            rent_web_boxes=tuple(as_non_empty_str(item, "rent_web_box") for item in (data.get("rent_web_boxes") or [])),
            sources=sources,
            effective_from=effective_from,
            effective_to=effective_to,
            last_reviewed_at=as_optional_str(data.get("last_reviewed_at"), "last_reviewed_at"),
            risk_level=RiskLevel(data["risk_level"]),
            validation_status=ValidationStatus(data["validation_status"]),
        )


@dataclass(frozen=True)
class RuleEvaluation:
    deduction_id: str
    status: RuleStatus
    estimated_amount: float
    reason: str
    missing_fields: tuple[str, ...] = ()
    missing_documents: tuple[str, ...] = ()
    sources: tuple[Source, ...] = ()
    risk_level: Literal["low", "medium", "high"] = "medium"
    confidence: float = 0.0


@dataclass
class TaxProfile:
    tax_year: int
    region: str
    filing_mode: str = "unknown"
    personal: dict[str, Any] = field(default_factory=dict)
    family: dict[str, Any] = field(default_factory=lambda: {"children": [], "ascendants": []})
    income: dict[str, Any] = field(default_factory=dict)
    withholdings: list[dict[str, Any]] = field(default_factory=list)
    expenses: Any = field(default_factory=dict)
    taxable_base: dict[str, Any] = field(default_factory=dict)
    deduction_candidates: list[str] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TaxProfile:
        require_keys(data, ["tax_year", "region"], "tax_profile")
        tax_year = as_tax_year(data["tax_year"], "tax_profile.tax_year")
        return cls(
            tax_year=tax_year,
            region=as_non_empty_str(data["region"], "tax_profile.region"),
            filing_mode=data.get("filing_mode", "unknown"),
            personal=dict(data.get("personal") or {}),
            family=dict(data.get("family") or {"children": [], "ascendants": []}),
            income=dict(data.get("income") or {}),
            withholdings=list(data.get("withholdings") or []),
            expenses=data.get("expenses") or {},
            taxable_base=dict(data.get("taxable_base") or {}),
            deduction_candidates=list(data.get("deduction_candidates") or []),
            documents=list(data.get("documents") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tax_year": self.tax_year,
            "region": self.region,
            "filing_mode": self.filing_mode,
            "personal": self.personal,
            "family": self.family,
            "income": self.income,
            "withholdings": self.withholdings,
            "expenses": self.expenses,
            "taxable_base": self.taxable_base,
            "deduction_candidates": self.deduction_candidates,
            "documents": self.documents,
        }


def require_keys(data: dict[str, Any], keys: list[str], context: str) -> None:
    missing = [key for key in keys if key not in data]
    if missing:
        raise ValidationError(f"Faltan campos obligatorios en {context}: {', '.join(missing)}")


def as_non_empty_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} debe ser texto no vacío")
    return value.strip()


def as_optional_str(value: Any, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} debe ser texto o null")
    return value.strip() or None


def as_optional_number(value: Any, field_name: str) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} debe ser numérico o null")
    return float(value)


def as_list(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValidationError(f"{field_name} debe ser una lista")
    return value


def as_tax_year(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 2000:
        raise ValidationError(f"{field_name} debe ser un entero válido")
    return int(value)


def as_optional_iso_date(value: Any, field_name: str) -> str | None:
    text = as_optional_str(value, field_name)
    if text is None:
        return None
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise ValidationError(f"{field_name} debe ser una fecha ISO YYYY-MM-DD") from exc
    return text
