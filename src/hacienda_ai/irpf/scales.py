"""Modelos y carga de escalas progresivas del IRPF.

Una `TaxScale` es una secuencia ordenada de `Bracket` (tramos) con la cita BOE
del precepto que la fija. Igual que las deducciones, las escalas se declaran
en JSON con `boe_id` + `content_hash` para que cualquier cambio de tramo
pueda detectarse por drift (no se modela un importe sin tocar su fuente).

Cada escala vive en uno de cuatro ejes:

- `scope`: `estatal` (LIRPF art. 63/66) o `autonomico` (norma de la CCAA o
  texto refundido autonómico). La parte autonómica del ahorro la fija el
  Estado en el art. 76 LIRPF, pero por uniformidad de modelo se sigue
  declarando como escala con `scope="estatal"` y `component="ahorro"`; la
  cuota autonómica del ahorro reutiliza esa misma escala.
- `component`: `general` (BLG) o `ahorro` (BLA).
- `tax_year` + `region` (None para estatal): localizan la escala.
- `effective_from`/`effective_to`: vigencia temporal aplicada con el devengo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from ..models import Source, ValidationError, ValidationStatus
from ..models._common import (
    as_list,
    as_non_empty_str,
    as_optional_number,
    as_optional_str,
    parse_iso_date,
    require_keys,
)

ScaleScope = Literal["estatal", "autonomico"]
ScaleComponent = Literal["general", "ahorro"]

_SUPPORTED_SCOPES: frozenset[str] = frozenset({"estatal", "autonomico"})
_SUPPORTED_COMPONENTS: frozenset[str] = frozenset({"general", "ahorro"})


@dataclass(frozen=True)
class Bracket:
    """Tramo de una escala progresiva.

    `up_to`: límite superior (inclusive) de la base sobre el que aplica este
    tramo. `None` solo en el último tramo (sin techo). `rate`: tipo aplicable
    a la porción de base cubierta por este tramo, expresado en tanto por uno
    (0.095 = 9,5%).
    """

    up_to: float | None
    rate: float

    def __post_init__(self) -> None:
        if not (0 <= self.rate <= 1):
            raise ValidationError(
                f"bracket.rate debe estar entre 0 y 1, recibido {self.rate}"
            )
        if self.up_to is not None and self.up_to <= 0:
            raise ValidationError(
                f"bracket.up_to debe ser positivo o null, recibido {self.up_to}"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Bracket":
        require_keys(data, ["rate"], "bracket")
        rate = as_optional_number(data["rate"], "bracket.rate")
        if rate is None:
            raise ValidationError("bracket.rate es obligatorio")
        return cls(
            up_to=as_optional_number(data.get("up_to"), "bracket.up_to"),
            rate=rate,
        )


@dataclass(frozen=True)
class TaxScale:
    id: str
    name: str
    description: str
    tax_year: int
    scope: ScaleScope
    component: ScaleComponent
    region: str | None
    brackets: tuple[Bracket, ...]
    sources: tuple[Source, ...]
    effective_from: date
    effective_to: date | None
    last_reviewed_at: date | None
    validation_status: ValidationStatus

    def __post_init__(self) -> None:
        if not self.brackets:
            raise ValidationError(f"TaxScale {self.id}: brackets vacíos")
        previous: float = 0.0
        for index, bracket in enumerate(self.brackets):
            is_last = index == len(self.brackets) - 1
            if bracket.up_to is None and not is_last:
                raise ValidationError(
                    f"TaxScale {self.id}: solo el último bracket puede tener up_to=null"
                )
            if bracket.up_to is not None:
                if bracket.up_to <= previous:
                    raise ValidationError(
                        f"TaxScale {self.id}: brackets deben ser estrictamente crecientes; "
                        f"tramo {index} (up_to={bracket.up_to}) ≤ anterior ({previous})"
                    )
                previous = bracket.up_to
        if (
            self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValidationError(
                f"TaxScale {self.id}: effective_to ({self.effective_to.isoformat()}) "
                f"anterior a effective_from ({self.effective_from.isoformat()})"
            )
        if self.scope == "autonomico" and self.region is None:
            raise ValidationError(
                f"TaxScale {self.id}: scope=autonomico requiere region"
            )

    def covers(self, devengo: date) -> bool:
        if devengo < self.effective_from:
            return False
        if self.effective_to is not None and devengo > self.effective_to:
            return False
        return True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TaxScale":
        require_keys(
            data,
            [
                "id",
                "name",
                "description",
                "tax_year",
                "scope",
                "component",
                "brackets",
                "sources",
                "effective_from",
                "validation_status",
            ],
            "tax_scale",
        )
        scope = as_non_empty_str(data["scope"], "tax_scale.scope")
        if scope not in _SUPPORTED_SCOPES:
            allowed = ", ".join(sorted(_SUPPORTED_SCOPES))
            raise ValidationError(
                f"tax_scale.scope '{scope}' no soportado; valores admitidos: {allowed}"
            )
        component = as_non_empty_str(data["component"], "tax_scale.component")
        if component not in _SUPPORTED_COMPONENTS:
            allowed = ", ".join(sorted(_SUPPORTED_COMPONENTS))
            raise ValidationError(
                f"tax_scale.component '{component}' no soportado; valores "
                f"admitidos: {allowed}"
            )
        tax_year = data["tax_year"]
        if not isinstance(tax_year, int) or tax_year < 2000:
            raise ValidationError("tax_scale.tax_year debe ser un entero válido")
        brackets_raw = as_list(data["brackets"], "tax_scale.brackets")
        brackets = tuple(Bracket.from_dict(b) for b in brackets_raw)
        sources_raw = as_list(data["sources"], "tax_scale.sources")
        sources = tuple(Source.from_dict(s) for s in sources_raw)
        if not sources:
            raise ValidationError(
                f"tax_scale {data['id']}: requiere al menos una fuente"
            )
        return cls(
            id=as_non_empty_str(data["id"], "tax_scale.id"),
            name=as_non_empty_str(data["name"], "tax_scale.name"),
            description=as_non_empty_str(data["description"], "tax_scale.description"),
            tax_year=tax_year,
            scope=scope,  # type: ignore[arg-type]
            component=component,  # type: ignore[arg-type]
            region=as_optional_str(data.get("region"), "tax_scale.region"),
            brackets=brackets,
            sources=sources,
            effective_from=parse_iso_date(data["effective_from"], "tax_scale.effective_from")
            or _raise_required("tax_scale.effective_from"),
            effective_to=parse_iso_date(data.get("effective_to"), "tax_scale.effective_to"),
            last_reviewed_at=parse_iso_date(
                data.get("last_reviewed_at"), "tax_scale.last_reviewed_at"
            ),
            validation_status=ValidationStatus(data["validation_status"]),
        )


def _raise_required(field: str) -> date:
    raise ValidationError(f"{field} es obligatorio")


DEFAULT_SCALES_DIR = Path(__file__).resolve().parent.parent / "data" / "escalas"


def load_tax_scales(path: Path | str = DEFAULT_SCALES_DIR) -> list[TaxScale]:
    """Carga escalas desde un directorio (uno o varios JSON) o un único fichero.

    Estructura JSON aceptada: `{"_meta": {...}, "scales": [...]}`. Las claves
    `_meta` (y cualquier otra desconocida) se ignoran; lo único obligatorio es
    `scales`. Identificadores duplicados disparan `ValidationError`.
    """
    root = Path(path)
    if root.is_file():
        files = [root]
    else:
        files = sorted(root.glob("*.json"))
    scales: list[TaxScale] = []
    seen: set[str] = set()
    for file_path in files:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValidationError(
                f"{file_path}: el JSON de escalas debe ser un objeto con clave 'scales'"
            )
        entries = raw.get("scales", [])
        if not isinstance(entries, list):
            raise ValidationError(f"{file_path}: 'scales' debe ser una lista")
        for entry in entries:
            scale = TaxScale.from_dict(entry)
            if scale.id in seen:
                raise ValidationError(f"TaxScale duplicada: {scale.id}")
            seen.add(scale.id)
            scales.append(scale)
    return scales


def select_scale(
    scales: list[TaxScale],
    *,
    tax_year: int,
    scope: ScaleScope,
    component: ScaleComponent,
    region: str | None,
    devengo: date,
) -> TaxScale | None:
    """Devuelve la escala que aplica al devengo dado.

    El emparejamiento es exacto en `tax_year`, `scope` y `component`. Para
    `scope=autonomico` se compara `region` case-insensitive; para
    `scope=estatal` se ignora `region`. Si hay varias candidatas (no debería)
    se devuelve la última cuyo intervalo cubre el devengo.
    """
    chosen: TaxScale | None = None
    for scale in scales:
        if scale.tax_year != tax_year:
            continue
        if scale.scope != scope:
            continue
        if scale.component != component:
            continue
        if scope == "autonomico":
            if scale.region is None or region is None:
                continue
            if scale.region.lower() != region.lower():
                continue
        if not scale.covers(devengo):
            continue
        chosen = scale
    return chosen
