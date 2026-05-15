"""Modelos tipados y validadores ligeros para datos fiscales.

El proyecto arranca sin dependencias externas para que la base fiscal pueda
validarse en cualquier entorno. Si más adelante se incorpora FastAPI, estos
modelos pueden migrarse a Pydantic manteniendo los mismos campos públicos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal


class ValidationError(ValueError):
    """Error de validación de datos fiscales o reglas."""


class Scope(str, Enum):
    ESTATAL = "estatal"
    AUTONOMICO = "autonomico"
    FORAL = "foral"
    LOCAL = "local"


class ForalTerritory(str, Enum):
    BIZKAIA = "bizkaia"
    GIPUZKOA = "gipuzkoa"
    ALAVA = "alava"
    NAVARRA = "navarra"


class SourceKind(str, Enum):
    """Jerarquía normativa de la fuente citada.

    Permite ordenar por rango (ley > reglamento > doctrina administrativa >
    jurisprudencia) y distinguir doctrina administrativa de pronunciamientos
    jurisdiccionales al presentar la respuesta al usuario.
    """

    LEY_ORGANICA = "ley_organica"
    LEY = "ley"
    REAL_DECRETO_LEGISLATIVO = "real_decreto_legislativo"
    REAL_DECRETO = "real_decreto"
    ORDEN_MINISTERIAL = "orden_ministerial"
    DGT_VINCULANTE = "dgt_vinculante"
    TEAC = "teac"
    TS = "ts"
    AN = "an"
    TSJ = "tsj"
    MANUAL_AEAT = "manual_aeat"
    INSTRUCCION_AEAT = "instruccion_aeat"
    PENDIENTE_VALIDACION = "pendiente_validacion"


class DeductionCategory(str, Enum):
    DEDUCCION = "deduccion"
    REDUCCION = "reduccion"
    EXENCION = "exencion"
    GASTO_DEDUCIBLE = "gasto_deducible"
    MINIMO_PERSONAL_FAMILIAR = "minimo_personal_familiar"
    COMPENSACION = "compensacion"
    AJUSTE = "ajuste"


class RiskLevel(str, Enum):
    BAJO = "bajo"
    MEDIO = "medio"
    ALTO = "alto"


class ValidationStatus(str, Enum):
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
    kind: SourceKind
    title: str
    url: str | None = None
    article: str | None = None
    paragraph: str | None = None
    boe_id: str | None = None
    content_hash: str | None = None
    checked_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Source":
        require_keys(data, ["kind", "title"], "source")
        kind_raw = as_non_empty_str(data["kind"], "source.kind")
        try:
            kind = SourceKind(kind_raw)
        except ValueError as exc:
            allowed = ", ".join(sorted(item.value for item in SourceKind))
            raise ValidationError(
                f"source.kind '{kind_raw}' no soportado; valores admitidos: {allowed}"
            ) from exc
        content_hash = _validate_content_hash(
            as_optional_str(data.get("content_hash"), "source.content_hash")
        )
        return cls(
            kind=kind,
            title=as_non_empty_str(data["title"], "source.title"),
            url=as_optional_str(data.get("url"), "source.url"),
            article=as_optional_str(data.get("article"), "source.article"),
            paragraph=as_optional_str(data.get("paragraph"), "source.paragraph"),
            boe_id=as_optional_str(data.get("boe_id"), "source.boe_id"),
            content_hash=content_hash,
            checked_at=as_optional_str(data.get("checked_at"), "source.checked_at"),
        )


@dataclass(frozen=True)
class Requirement:
    field: str
    operator: str
    value: Any = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Requirement":
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
class Calculation:
    type: str
    base_field: str | None = None
    percentage: float | None = None
    cap: float | None = None
    fixed_amount: float | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Calculation":
        require_keys(data, ["type"], "calculation")
        calculation_type = as_non_empty_str(data["type"], "calculation.type")
        if calculation_type not in {"manual_review", "amount_field", "percentage_with_cap", "fixed_amount"}:
            raise ValidationError(f"Tipo de cálculo no soportado: {calculation_type}")
        percentage = as_optional_number(data.get("percentage"), "calculation.percentage")
        cap = as_optional_number(data.get("cap"), "calculation.cap")
        fixed_amount = as_optional_number(data.get("fixed_amount"), "calculation.fixed_amount")
        if calculation_type == "percentage_with_cap" and percentage is not None and not (0 <= percentage <= 1):
            raise ValidationError("calculation.percentage debe expresarse entre 0 y 1")
        return cls(
            type=calculation_type,
            base_field=as_optional_str(data.get("base_field"), "calculation.base_field"),
            percentage=percentage,
            cap=cap,
            fixed_amount=fixed_amount,
        )


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
    taxable_base_limits: dict[str, Any]
    incompatibilities: tuple[str, ...]
    required_documents: tuple[str, ...]
    rent_web_boxes: tuple[str, ...]
    sources: tuple[Source, ...]
    effective_from: str | None
    effective_to: str | None
    last_reviewed_at: str | None
    risk_level: RiskLevel
    validation_status: ValidationStatus
    foral_territory: ForalTerritory | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Deduction":
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
        tax_year = data["tax_year"]
        if not isinstance(tax_year, int) or tax_year < 2000:
            raise ValidationError("tax_year debe ser un entero válido")
        scope = Scope(data["scope"])
        validation_status = ValidationStatus(data["validation_status"])
        foral_territory_raw = as_optional_str(data.get("foral_territory"), "foral_territory")
        foral_territory: ForalTerritory | None
        if foral_territory_raw is None:
            foral_territory = None
        else:
            try:
                foral_territory = ForalTerritory(foral_territory_raw)
            except ValueError as exc:
                allowed = ", ".join(sorted(item.value for item in ForalTerritory))
                raise ValidationError(
                    f"foral_territory '{foral_territory_raw}' no soportado; valores admitidos: {allowed}"
                ) from exc
        if scope == Scope.FORAL and foral_territory is None:
            raise ValidationError(
                "scope=foral requiere foral_territory (bizkaia, gipuzkoa, alava o navarra)"
            )
        if foral_territory is not None and scope != Scope.FORAL:
            raise ValidationError(
                "foral_territory solo es válido cuando scope=foral"
            )
        if validation_status == ValidationStatus.VALIDADA:
            has_anchor = any(
                source.boe_id is not None and source.content_hash is not None
                for source in sources
            )
            if not has_anchor:
                raise ValidationError(
                    "Una deducción validada exige al menos una fuente con boe_id y content_hash"
                )
        return cls(
            id=as_non_empty_str(data["id"], "id"),
            name=as_non_empty_str(data["name"], "name"),
            description=as_non_empty_str(data["description"], "description"),
            tax_year=tax_year,
            scope=scope,
            region=as_optional_str(data.get("region"), "region"),
            category=DeductionCategory(data["category"]),
            requirements=tuple(Requirement.from_dict(item) for item in as_list(data["requirements"], "requirements")),
            calculation=Calculation.from_dict(data["calculation"]),
            limit=as_optional_number(data.get("limit"), "limit"),
            taxable_base_limits=dict(data.get("taxable_base_limits") or {}),
            incompatibilities=tuple(
                as_non_empty_str(item, "incompatibility")
                for item in data.get("incompatibilities", [])
            ),
            required_documents=tuple(
                as_non_empty_str(item, "required_document")
                for item in data.get("required_documents", [])
            ),
            rent_web_boxes=tuple(
                as_non_empty_str(item, "rent_web_box")
                for item in data.get("rent_web_boxes", [])
            ),
            sources=sources,
            effective_from=as_optional_str(data.get("effective_from"), "effective_from"),
            effective_to=as_optional_str(data.get("effective_to"), "effective_to"),
            last_reviewed_at=as_optional_str(data.get("last_reviewed_at"), "last_reviewed_at"),
            risk_level=RiskLevel(data["risk_level"]),
            validation_status=validation_status,
            foral_territory=foral_territory,
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
    expenses: dict[str, float] = field(default_factory=dict)
    deduction_candidates: list[str] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaxProfile":
        require_keys(data, ["tax_year", "region"], "tax_profile")
        tax_year = data["tax_year"]
        if not isinstance(tax_year, int) or tax_year < 2000:
            raise ValidationError("tax_profile.tax_year debe ser un entero válido")
        return cls(
            tax_year=tax_year,
            region=as_non_empty_str(data["region"], "tax_profile.region"),
            filing_mode=data.get("filing_mode", "unknown"),
            personal=dict(data.get("personal") or {}),
            family=dict(data.get("family") or {"children": [], "ascendants": []}),
            income=dict(data.get("income") or {}),
            withholdings=list(data.get("withholdings") or []),
            expenses=_parse_expenses(data.get("expenses") or {}),
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


_HEX_CHARS = frozenset("0123456789abcdef")


def _validate_content_hash(value: str | None) -> str | None:
    """Valida formato SHA-256 hex (64 caracteres) y normaliza a minúsculas."""
    if value is None:
        return None
    normalized = value.lower()
    if len(normalized) != 64 or any(c not in _HEX_CHARS for c in normalized):
        raise ValidationError(
            "source.content_hash debe ser SHA-256 en hexadecimal (64 caracteres)"
        )
    return normalized


def _parse_expenses(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise ValidationError("tax_profile.expenses debe ser un diccionario clave→importe")
    result: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key:
            raise ValidationError("tax_profile.expenses: las claves deben ser texto no vacío")
        if not isinstance(raw, (int, float)) or isinstance(raw, bool):
            raise ValidationError(f"tax_profile.expenses.{key} debe ser numérico")
        result[key] = float(raw)
    return result
