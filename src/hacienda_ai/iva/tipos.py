"""Tipos impositivos del IVA español y cálculo de cuota.

Vigentes según LIVA (Ley 37/1992, BOE-A-1992-28740):

- **General**: 21% — art. 90.1.
- **Reducido**: 10% — art. 91.1 (bienes/servicios listados).
- **Superreducido**: 4% — art. 91.2 (pan común, leche, huevos, frutas,
  hortalizas, libros, medicamentos uso humano, prótesis e implantes
  internos, vehículos para personas con movilidad reducida, etc.).
- **Cero (0%)**: operaciones gravadas a tipo cero — principalmente
  exportaciones y asimiladas (arts. 21-25); también medidas
  coyunturales (p. ej. alimentos básicos por RD-Ley 20/2022, con
  prórrogas hasta 2024). Hay diferencia material entre "exento" (no
  sujeto a gravamen, normalmente sin derecho a deducir el soportado)
  y "cero" (gravado pero al 0%, con derecho a deducir): mantenemos
  ambos en el enum.
- **Exento**: operaciones no sujetas (arts. 20-25 LIVA) — servicios
  médicos, enseñanza reglada, operaciones financieras, alquileres
  de vivienda, etc. Cuota = 0 € y sin derecho a deducción del IVA
  soportado (con matices del art. 20.tres).

NO modelamos las medidas coyunturales con vigencia limitada (alimentos
0% por RD-Ley 20/2022, electricidad reducida temporal, etc.): el LLM
debe consultar el BOE consolidado para el devengo concreto antes de
afirmar el tipo aplicable en una fecha histórica reciente.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..models import Source
from ..models.norma import SourceKind

LIVA_BOE_ID = "BOE-A-1992-28740"


class IVATipo(str, Enum):
    """Tipos impositivos del IVA español.

    `EXENTO` indica operación no sujeta (cuota = 0, normalmente sin
    derecho a deducción del soportado). `CERO` indica operación
    gravada al 0% (con derecho a deducción, p. ej. exportaciones).
    """

    GENERAL = "general"
    REDUCIDO = "reducido"
    SUPERREDUCIDO = "superreducido"
    CERO = "cero"
    EXENTO = "exento"


# Tasas vigentes. `None` en EXENTO refleja que no hay gravamen
# aplicable, no que el tipo sea 0% — distinguir es importante para el
# tratamiento del IVA soportado.
IVA_RATES: dict[IVATipo, float | None] = {
    IVATipo.GENERAL: 0.21,
    IVATipo.REDUCIDO: 0.10,
    IVATipo.SUPERREDUCIDO: 0.04,
    IVATipo.CERO: 0.0,
    IVATipo.EXENTO: None,
}


# Cita pinpoint por tipo: el artículo de cabecera que define la tasa.
# El catálogo de operaciones (en `operations.py`) puede afinar a
# apartados específicos cuando la asociación operación → tipo está
# regulada con detalle (p. ej. art. 91.1.2º.1º para transporte de
# viajeros), pero el "marco" del tipo siempre es uno de estos.
IVA_SOURCES: dict[IVATipo, Source] = {
    IVATipo.GENERAL: Source(
        kind=SourceKind.LEY,
        title="LIVA art. 90 — tipo general 21%",
        boe_id=LIVA_BOE_ID,
        article="art. 90",
    ),
    IVATipo.REDUCIDO: Source(
        kind=SourceKind.LEY,
        title="LIVA art. 91 — tipo reducido 10%",
        boe_id=LIVA_BOE_ID,
        article="art. 91",
        paragraph="1",
    ),
    IVATipo.SUPERREDUCIDO: Source(
        kind=SourceKind.LEY,
        title="LIVA art. 91 — tipo superreducido 4%",
        boe_id=LIVA_BOE_ID,
        article="art. 91",
        paragraph="2",
    ),
    IVATipo.CERO: Source(
        kind=SourceKind.LEY,
        title="LIVA arts. 21-25 — operaciones exoneradas con derecho a deducción",
        boe_id=LIVA_BOE_ID,
        article="art. 21",
    ),
    IVATipo.EXENTO: Source(
        kind=SourceKind.LEY,
        title="LIVA art. 20 — exenciones en operaciones interiores",
        boe_id=LIVA_BOE_ID,
        article="art. 20",
    ),
}


@dataclass(frozen=True)
class IVAQuota:
    """Resultado del cálculo de cuota IVA sobre una base imponible.

    `cuota` es `None` cuando la operación está EXENTA (no se devenga
    cuota) — distinto de `0.0` que sí es una cuota legalmente
    cuantificable (operaciones a tipo cero).
    """

    base_imponible: float
    tipo: IVATipo
    rate: float | None
    cuota: float | None
    total: float | None
    source: Source
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_imponible": self.base_imponible,
            "tipo": self.tipo.value,
            "rate": self.rate,
            "cuota": self.cuota,
            "total": self.total,
            "source": {
                "boe_id": self.source.boe_id,
                "article": self.source.article,
                "paragraph": self.source.paragraph,
                "title": self.source.title,
            },
            "note": self.note,
        }


class IVAComputationError(ValueError):
    """Datos inválidos para el cálculo de IVA."""


def compute_iva_quota(base_imponible: float, tipo: IVATipo) -> IVAQuota:
    """Calcula la cuota IVA aplicable a una base imponible.

    Para los tipos `GENERAL`/`REDUCIDO`/`SUPERREDUCIDO`/`CERO`:
    `cuota = base × rate` y `total = base + cuota`. Para `EXENTO`,
    `cuota=None` y `total=base` con una nota explicativa: la
    operación no genera cuota pero tampoco permite deducir el IVA
    soportado (con las salvedades del art. 20.tres LIVA).

    Errores controlados (lanza `IVAComputationError`):
    - `base_imponible` negativa: el cálculo aritmético funcionaría
      pero semánticamente nunca es válido.
    - `tipo` no reconocido: defensa frente a inputs serializados que
      no se mapean al enum.
    """
    if not isinstance(base_imponible, (int, float)) or isinstance(
        base_imponible, bool
    ):
        raise IVAComputationError(
            f"base_imponible debe ser numérica; recibido {type(base_imponible).__name__}"
        )
    if base_imponible < 0:
        raise IVAComputationError(
            f"base_imponible no puede ser negativa: {base_imponible}"
        )
    if tipo not in IVA_RATES:
        raise IVAComputationError(f"tipo IVA no reconocido: {tipo!r}")

    rate = IVA_RATES[tipo]
    source = IVA_SOURCES[tipo]

    if tipo == IVATipo.EXENTO:
        return IVAQuota(
            base_imponible=float(base_imponible),
            tipo=tipo,
            rate=None,
            cuota=None,
            total=float(base_imponible),
            source=source,
            note=(
                "Operación exenta de IVA: no se devenga cuota. El "
                "sujeto que la realiza no puede, en general, deducir "
                "el IVA soportado en los inputs (LIVA art. 20.tres "
                "establece las excepciones)."
            ),
        )

    assert rate is not None  # nosec — los demás tipos tienen rate.
    cuota = round(float(base_imponible) * rate, 2)
    total = round(float(base_imponible) + cuota, 2)
    return IVAQuota(
        base_imponible=float(base_imponible),
        tipo=tipo,
        rate=rate,
        cuota=cuota,
        total=total,
        source=source,
        note="" if tipo != IVATipo.CERO else (
            "Operación gravada al 0% (no es lo mismo que exenta): el "
            "sujeto sí tiene derecho a deducir el IVA soportado."
        ),
    )


__all__ = [
    "IVAComputationError",
    "IVAQuota",
    "IVATipo",
    "IVA_RATES",
    "IVA_SOURCES",
    "LIVA_BOE_ID",
    "compute_iva_quota",
]
