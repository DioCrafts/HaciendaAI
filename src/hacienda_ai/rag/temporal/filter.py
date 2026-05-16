"""Filtro temporal duro: garantiza que el retrieval respeta la vigencia.

Aplicado SOBRE los resultados del vector store (no en lugar de). El
store ya filtra a nivel motor; este módulo es defensa en profundidad:
detecta chunks que se hayan colado por metadata corrupta o lookups
imprecisos, y los excluye con motivo registrado.

Modos:

- **`STRICT`**: chunks sin `effective_from` se rechazan (no podemos
  verificar vigencia). Si falta `fecha_devengo` en el query, lanza
  `StrictTemporalFilterError`.
- **`WARN`**: chunks sin metadata temporal se aceptan con disclaimer
  en el report. Si falta `fecha_devengo` en el query, asume hoy y
  registra warning.
- **`OFF`**: no se aplica filtro adicional (solo el del store). Útil
  para queries históricas explícitas sobre normativa derogada.

Devuelve un `TemporalFilterReport` con la lista de matches filtrados,
los rechazados por motivo, y el modo usado. El caller (típicamente
`citation_grounding`) lo persiste para auditoría.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

from ..vector import VectorMatch, VectorQuery

logger = logging.getLogger(__name__)


class TemporalEnforcementMode(str, Enum):
    """Política del filtro temporal.

    `STRICT` es la recomendada para producción. `WARN` para desarrollo.
    `OFF` SOLO para queries históricas explícitas, donde el usuario
    asume el riesgo de citar normativa antigua.
    """

    STRICT = "strict"
    WARN = "warn"
    OFF = "off"


class StrictTemporalFilterError(RuntimeError):
    """En modo STRICT falta `fecha_devengo` en el query."""


@dataclass
class TemporalFilterReport:
    """Resultado del filtro temporal."""

    mode: TemporalEnforcementMode
    fecha_devengo: date
    fecha_devengo_explicit: bool  # ¿vino del query o se asumió hoy?
    accepted: list[VectorMatch] = field(default_factory=list)
    rejected: list[tuple[VectorMatch, str]] = field(default_factory=list)
    atemporal_with_disclaimer: list[VectorMatch] = field(default_factory=list)

    @property
    def has_warnings(self) -> bool:
        return (
            not self.fecha_devengo_explicit
            or bool(self.atemporal_with_disclaimer)
        )

    def warnings_text(self) -> list[str]:
        out: list[str] = []
        if not self.fecha_devengo_explicit:
            out.append(
                f"fecha_devengo no provista: asumida {self.fecha_devengo.isoformat()} "
                "(hoy). Las respuestas pueden citar normativa cuya vigencia "
                "no se ha verificado contra la fecha real del hecho imponible."
            )
        if self.atemporal_with_disclaimer:
            ids = [m.chunk.chunk_id for m in self.atemporal_with_disclaimer]
            out.append(
                f"{len(ids)} chunks sin metadata temporal aceptados con "
                "disclaimer (no se ha podido verificar su vigencia): "
                f"{ids[:5]}..."
                if len(ids) > 5
                else f"{len(ids)} chunks sin metadata temporal aceptados con "
                f"disclaimer: {ids}"
            )
        return out


def require_fecha_devengo(
    query: VectorQuery,
    *,
    mode: TemporalEnforcementMode,
    today: date | None = None,
) -> tuple[date, bool]:
    """Resuelve la `fecha_devengo` efectiva según el modo.

    Devuelve `(fecha_devengo, explicit)` donde `explicit` indica si
    vino del query. En `STRICT` lanza si falta; en `WARN` asume hoy.
    En `OFF` también asume hoy pero sin warning.
    """
    if query.fecha_devengo is not None:
        return query.fecha_devengo, True
    if mode == TemporalEnforcementMode.STRICT:
        raise StrictTemporalFilterError(
            "fecha_devengo es obligatoria en modo STRICT. "
            "Si la respuesta es a fecha de hoy, pásala explícitamente "
            "(`VectorQuery(fecha_devengo=date.today())`). "
            "Si la query es histórica, usa modo OFF."
        )
    fallback = today or date.today()
    if mode == TemporalEnforcementMode.WARN:
        logger.warning(
            "fecha_devengo ausente en query; asumiendo %s. "
            "Considera modo STRICT en producción.",
            fallback.isoformat(),
        )
    return fallback, False


def enforce_temporal_filter(
    matches: list[VectorMatch],
    query: VectorQuery,
    *,
    mode: TemporalEnforcementMode = TemporalEnforcementMode.STRICT,
    today: date | None = None,
) -> TemporalFilterReport:
    """Aplica el filtro temporal duro y devuelve un report auditable.

    Reglas:
    - Chunks con `effective_from > fecha_devengo` → rechazados
      ("norma posterior al hecho imponible").
    - Chunks con `effective_to < fecha_devengo` → rechazados
      ("norma derogada en la fecha").
    - Chunks sin `effective_from`:
      * STRICT → rechazados ("metadata temporal ausente").
      * WARN → aceptados con disclaimer.
      * OFF → aceptados sin disclaimer.
    """
    fecha_devengo, explicit = require_fecha_devengo(
        query, mode=mode, today=today
    )
    report = TemporalFilterReport(
        mode=mode,
        fecha_devengo=fecha_devengo,
        fecha_devengo_explicit=explicit,
    )

    for match in matches:
        metadata = match.chunk.metadata
        eff_from = _parse_date(metadata.get("effective_from"))
        eff_to = _parse_date(metadata.get("effective_to"))

        if eff_from is None:
            if mode == TemporalEnforcementMode.STRICT:
                report.rejected.append(
                    (match, "metadata.effective_from ausente (modo STRICT)")
                )
                continue
            if mode == TemporalEnforcementMode.WARN:
                report.atemporal_with_disclaimer.append(match)
            report.accepted.append(match)
            continue

        if eff_from > fecha_devengo:
            report.rejected.append(
                (
                    match,
                    f"norma posterior al hecho imponible "
                    f"(vigente desde {eff_from.isoformat()}, "
                    f"devengo {fecha_devengo.isoformat()})",
                )
            )
            continue
        if eff_to is not None and eff_to < fecha_devengo:
            report.rejected.append(
                (
                    match,
                    f"norma derogada antes del hecho imponible "
                    f"(vigente hasta {eff_to.isoformat()}, "
                    f"devengo {fecha_devengo.isoformat()})",
                )
            )
            continue
        report.accepted.append(match)

    return report


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None
