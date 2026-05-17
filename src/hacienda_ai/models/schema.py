"""Esquema de datos fiscales: enums, fuentes, deducciones y perfil.

El proyecto arranca sin dependencias externas para que la base fiscal pueda
validarse en cualquier entorno. Si más adelante se incorpora FastAPI, estos
modelos pueden migrarse a Pydantic manteniendo los mismos campos públicos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Literal

from ._common import (
    ValidationError,
    as_list,
    as_non_empty_str,
    as_optional_number,
    as_optional_str,
    is_regional_bulletin_id,
    is_state_bulletin_id,
    parse_iso_date,
    require_keys,
    validate_content_hash,
)
from .norma import SourceKind


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
    "requires_manual_calculation",
    "requires_user_choice",
]


RiskLiteral = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class Source:
    """Cita pinpoint hacia una norma (artículo + apartado opcional).

    `boe_id` enlaza con `Norma.boe_id` en un `NormaRegistry`. La vigencia y el
    estado (vigente/derogada/...) viven en `VersionNorma`, no aquí: una `Source`
    es solo un puntero textual.
    """

    kind: SourceKind
    title: str
    url: str | None = None
    article: str | None = None
    paragraph: str | None = None
    boe_id: str | None = None
    content_hash: str | None = None
    checked_at: date | None = None

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
        content_hash = validate_content_hash(
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
            checked_at=parse_iso_date(data.get("checked_at"), "source.checked_at"),
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
class Tier:
    """Tramo de un cálculo progresivo (`tiered_progressive`).

    `up_to`: límite superior de la BASE de este tramo, no acumulado de
    importe. En donativos Ley 49/2002: tier1 cubre los primeros 250 € de
    donativo (`up_to=250`), tier2 cubre el resto (`up_to=None` = sin
    techo). `percentage` se aplica a esa porción.

    `alternate_percentage` + `alternate_when_field`: ramificación
    declarativa para casos como fidelización en donativos (45% en lugar
    de 40% cuando el contribuyente ha igualado o superado el donativo a
    la misma entidad los 2 ejercicios anteriores). Si el campo del
    perfil existe y vale `True`, se usa `alternate_percentage`; en
    cualquier otro caso, `percentage`. Default conservador: el tramo
    general.
    """

    up_to: float | None
    percentage: float
    alternate_percentage: float | None = None
    alternate_when_field: str | None = None

    def __post_init__(self) -> None:
        if not (0 <= self.percentage <= 1):
            raise ValidationError(
                f"tier.percentage debe estar entre 0 y 1, recibido {self.percentage}"
            )
        if self.alternate_percentage is not None and not (
            0 <= self.alternate_percentage <= 1
        ):
            raise ValidationError(
                "tier.alternate_percentage debe estar entre 0 y 1, recibido "
                f"{self.alternate_percentage}"
            )
        if (self.alternate_percentage is None) != (self.alternate_when_field is None):
            raise ValidationError(
                "tier.alternate_percentage y alternate_when_field deben "
                "declararse juntos o no declararse ninguno"
            )
        if self.up_to is not None and self.up_to <= 0:
            raise ValidationError(
                f"tier.up_to debe ser positivo o null, recibido {self.up_to}"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Tier":
        require_keys(data, ["percentage"], "tier")
        up_to = as_optional_number(data.get("up_to"), "tier.up_to")
        percentage_value = as_optional_number(data["percentage"], "tier.percentage")
        if percentage_value is None:
            raise ValidationError("tier.percentage es obligatorio")
        return cls(
            up_to=up_to,
            percentage=percentage_value,
            alternate_percentage=as_optional_number(
                data.get("alternate_percentage"), "tier.alternate_percentage"
            ),
            alternate_when_field=as_optional_str(
                data.get("alternate_when_field"), "tier.alternate_when_field"
            ),
        )


_SUPPORTED_CALC_TYPES = frozenset(
    {
        "manual_review",
        "amount_field",
        "percentage_with_cap",
        "fixed_amount",
        "tiered_progressive",
    }
)


@dataclass(frozen=True)
class Calculation:
    type: str
    base_field: str | None = None
    percentage: float | None = None
    cap: float | None = None
    fixed_amount: float | None = None
    tiers: tuple[Tier, ...] = ()
    cap_field: str | None = None
    cap_percentage: float | None = None

    def __post_init__(self) -> None:
        if self.type == "tiered_progressive":
            if not self.tiers:
                raise ValidationError(
                    "calculation.type=tiered_progressive requiere al menos un tier"
                )
            if self.base_field is None:
                raise ValidationError(
                    "calculation.type=tiered_progressive requiere base_field"
                )
            # Solo el último tramo puede tener `up_to=None`; los demás deben
            # estar ordenados de forma estrictamente creciente.
            previous: float = 0.0
            for index, tier in enumerate(self.tiers):
                is_last = index == len(self.tiers) - 1
                if tier.up_to is None and not is_last:
                    raise ValidationError(
                        "Solo el último tier puede tener up_to=null"
                    )
                if tier.up_to is not None:
                    if tier.up_to <= previous:
                        raise ValidationError(
                            f"tiers deben ser estrictamente crecientes; "
                            f"tier {index} (up_to={tier.up_to}) ≤ anterior ({previous})"
                        )
                    previous = tier.up_to
        if (self.cap_field is None) != (self.cap_percentage is None):
            raise ValidationError(
                "calculation.cap_field y cap_percentage deben declararse "
                "juntos o no declararse ninguno"
            )
        if self.cap_percentage is not None and not (0 <= self.cap_percentage <= 1):
            raise ValidationError(
                "calculation.cap_percentage debe estar entre 0 y 1, recibido "
                f"{self.cap_percentage}"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Calculation":
        require_keys(data, ["type"], "calculation")
        calculation_type = as_non_empty_str(data["type"], "calculation.type")
        if calculation_type not in _SUPPORTED_CALC_TYPES:
            raise ValidationError(f"Tipo de cálculo no soportado: {calculation_type}")
        percentage = as_optional_number(data.get("percentage"), "calculation.percentage")
        cap = as_optional_number(data.get("cap"), "calculation.cap")
        fixed_amount = as_optional_number(data.get("fixed_amount"), "calculation.fixed_amount")
        if calculation_type == "percentage_with_cap" and percentage is not None and not (0 <= percentage <= 1):
            raise ValidationError("calculation.percentage debe expresarse entre 0 y 1")
        tiers_raw = data.get("tiers") or []
        if not isinstance(tiers_raw, list):
            raise ValidationError("calculation.tiers debe ser una lista")
        tiers = tuple(Tier.from_dict(item) for item in tiers_raw)
        cap_field = as_optional_str(data.get("cap_field"), "calculation.cap_field")
        cap_percentage = as_optional_number(
            data.get("cap_percentage"), "calculation.cap_percentage"
        )
        return cls(
            type=calculation_type,
            base_field=as_optional_str(data.get("base_field"), "calculation.base_field"),
            percentage=percentage,
            cap=cap,
            fixed_amount=fixed_amount,
            tiers=tiers,
            cap_field=cap_field,
            cap_percentage=cap_percentage,
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
    effective_from: date | None
    effective_to: date | None
    last_reviewed_at: date | None
    risk_level: RiskLevel
    validation_status: ValidationStatus
    foral_territory: ForalTerritory | None = None

    def __post_init__(self) -> None:
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValidationError(
                f"Deducción {self.id}: effective_to ({self.effective_to.isoformat()}) "
                f"anterior a effective_from ({self.effective_from.isoformat()})"
            )

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
            raise ValidationError(
                "Cada deducción debe incluir al menos una fuente o una marca pendiente de fuente"
            )
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
            has_state_anchor = any(
                source.boe_id is not None
                and is_state_bulletin_id(source.boe_id)
                and source.content_hash is not None
                for source in sources
            )
            has_regional_anchor = any(
                source.boe_id is not None and is_regional_bulletin_id(source.boe_id)
                for source in sources
            )
            if not (has_state_anchor or has_regional_anchor):
                raise ValidationError(
                    "Una deducción validada exige al menos una fuente anclada a "
                    "BOE estatal (boe_id BOE-A-... + content_hash) o a un "
                    "boletín autonómico/foral reconocido "
                    "(BOCM-, DOGC-, BOPV-, BON-, DOG-, etc.). El content_hash "
                    "es obligatorio solo para fuentes BOE estatales mientras "
                    "no exista verificador para el resto de boletines."
                )
        return cls(
            id=as_non_empty_str(data["id"], "id"),
            name=as_non_empty_str(data["name"], "name"),
            description=as_non_empty_str(data["description"], "description"),
            tax_year=tax_year,
            scope=scope,
            region=as_optional_str(data.get("region"), "region"),
            category=DeductionCategory(data["category"]),
            requirements=tuple(
                Requirement.from_dict(item) for item in as_list(data["requirements"], "requirements")
            ),
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
            effective_from=parse_iso_date(data.get("effective_from"), "effective_from"),
            effective_to=parse_iso_date(data.get("effective_to"), "effective_to"),
            last_reviewed_at=parse_iso_date(data.get("last_reviewed_at"), "last_reviewed_at"),
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
    risk_level: RiskLiteral = "medium"
    confidence: float = 0.0


@dataclass
class TaxProfile:
    tax_year: int
    region: str
    devengo_date: date | None = None
    filing_mode: str = "unknown"
    personal: dict[str, Any] = field(default_factory=dict)
    family: dict[str, Any] = field(default_factory=lambda: {"children": [], "ascendants": []})
    income: dict[str, Any] = field(default_factory=dict)
    withholdings: list[dict[str, Any]] = field(default_factory=list)
    expenses: dict[str, float] = field(default_factory=dict)
    deduction_candidates: list[str] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)

    def effective_devengo_date(self) -> date:
        """Fecha del hecho imponible aplicable.

        En IRPF, el devengo general es el 31 de diciembre del ejercicio salvo
        causas de cese (fallecimiento, fin de residencia). Si el perfil no
        especifica `devengo_date`, se asume 31-dic del `tax_year`.
        """
        return self.devengo_date if self.devengo_date is not None else date(self.tax_year, 12, 31)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaxProfile":
        require_keys(data, ["tax_year", "region"], "tax_profile")
        tax_year = data["tax_year"]
        if not isinstance(tax_year, int) or tax_year < 2000:
            raise ValidationError("tax_profile.tax_year debe ser un entero válido")
        devengo_date = parse_iso_date(data.get("devengo_date"), "tax_profile.devengo_date")
        if devengo_date is not None and devengo_date.year != tax_year:
            raise ValidationError(
                f"tax_profile.devengo_date ({devengo_date.isoformat()}) "
                f"debe pertenecer al tax_year {tax_year}"
            )
        return cls(
            tax_year=tax_year,
            region=as_non_empty_str(data["region"], "tax_profile.region"),
            devengo_date=devengo_date,
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
            "devengo_date": self.devengo_date.isoformat() if self.devengo_date is not None else None,
            "filing_mode": self.filing_mode,
            "personal": self.personal,
            "family": self.family,
            "income": self.income,
            "withholdings": self.withholdings,
            "expenses": self.expenses,
            "deduction_candidates": self.deduction_candidates,
            "documents": self.documents,
        }


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
