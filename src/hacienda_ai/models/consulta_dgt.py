"""Modelo de Consulta Vinculante de la DGT.

Las consultas vinculantes de la Dirección General de Tributos son el
criterio administrativo oficial sobre interpretación de la normativa
tributaria. Son vinculantes para la AEAT en supuestos idénticos (art.
89 LGT) — el LLM puede citarlas como criterio firme de la
Administración, distinguiendo siempre del criterio jurisprudencial
(que prevalece) y de la mera consulta no vinculante.

Identificador canónico: número de consulta `V<NNNN>-<YY>` (la "V" es
de "vinculante"; las no vinculantes empiezan por "C" y no entran a este
corpus). Ejemplos: `V0001-24`, `V2398-23`, `V0023-22`.

Campos críticos para citar sin alucinar:

- `numero`: identificador estable.
- `fecha_salida`: fecha de la contestación DGT. Es la que cuenta a
  efectos de cómputo de plazos de "consulta posterior a tu hecho
  imponible".
- `impuesto`: a qué tributo se refiere. Una consulta puede tocar varios
  (típico: IRPF + IRNR para no residentes), pero el principal va aquí.
- `asunto`: una línea con la materia.
- `cuestion_planteada`: texto del consultante. Importante porque el
  criterio DGT se aplica al supuesto descrito, no a casos análogos sin
  más; el LLM debe poder citar el supuesto para distinguir.
- `contestacion_completa`: la respuesta jurídica entera. Persistida
  para auditoría.
- `criterio`: extracto del criterio doctrinal (heurístico). Marcado con
  `criterio_confidence` (`auto`/`manual`) para distinguir extracción
  automática de validación humana.
- `normativa`: artículos citados en el cuerpo (`Ley 35/2006 art. 19.2.e)`,
  etc.). Útil para invalidar el corpus cuando la norma cambia.
- `content_hash`: SHA-256 del texto normalizado de la contestación,
  para detectar correcciones futuras.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any

from ._common import (
    ValidationError,
    as_non_empty_str,
    as_optional_str,
    parse_iso_date,
    require_iso_date,
    require_keys,
    validate_content_hash,
)


class Impuesto(str, Enum):
    """Tributos a los que una consulta DGT puede referirse principalmente.

    `OTRO` cubre figuras tributarias menos frecuentes (impuestos
    especiales, primas de seguros, IAE…) y cuestiones de procedimiento
    tributario (LGT) que no encajan en un tributo concreto.
    """

    IRPF = "irpf"
    IVA = "iva"
    IS = "is"
    IP = "ip"
    ISD = "isd"
    IRNR = "irnr"
    ITP_AJD = "itp_ajd"
    IIVTNU = "iivtnu"
    IBI = "ibi"
    IAE = "iae"
    LGT = "lgt"
    OTRO = "otro"


class CriterioConfidence(str, Enum):
    """Confianza en el extracto de criterio. Mismo patrón que `RatioConfidence`."""

    AUTO = "auto"
    MANUAL = "manual"


@dataclass(frozen=True)
class ConsultaDGT:
    """Consulta vinculante DGT con metadatos suficientes para citarla."""

    numero: str
    fecha_salida: date
    fecha_entrada: date | None
    impuesto: Impuesto
    asunto: str
    cuestion_planteada: str
    contestacion_completa: str
    criterio: str | None
    criterio_confidence: CriterioConfidence
    normativa: tuple[str, ...]
    url: str | None
    content_hash: str
    last_fetched_at: date

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConsultaDGT":
        require_keys(
            data,
            [
                "numero",
                "fecha_salida",
                "impuesto",
                "asunto",
                "cuestion_planteada",
                "contestacion_completa",
                "criterio_confidence",
                "content_hash",
                "last_fetched_at",
            ],
            "consulta_dgt",
        )
        impuesto_raw = as_non_empty_str(
            data["impuesto"], "consulta_dgt.impuesto"
        )
        try:
            impuesto = Impuesto(impuesto_raw)
        except ValueError as exc:
            allowed = ", ".join(sorted(i.value for i in Impuesto))
            raise ValidationError(
                f"consulta_dgt.impuesto '{impuesto_raw}' no soportado; admitidos: {allowed}"
            ) from exc

        conf_raw = as_non_empty_str(
            data["criterio_confidence"], "consulta_dgt.criterio_confidence"
        )
        try:
            confidence = CriterioConfidence(conf_raw)
        except ValueError as exc:
            allowed = ", ".join(sorted(c.value for c in CriterioConfidence))
            raise ValidationError(
                f"consulta_dgt.criterio_confidence '{conf_raw}' no soportado; admitidos: {allowed}"
            ) from exc

        content_hash = validate_content_hash(
            as_non_empty_str(
                data["content_hash"], "consulta_dgt.content_hash"
            )
        )
        if content_hash is None:
            raise ValidationError("consulta_dgt.content_hash es obligatorio")

        normativa_raw = data.get("normativa", [])
        if not isinstance(normativa_raw, list):
            raise ValidationError(
                "consulta_dgt.normativa debe ser lista de strings"
            )
        normativa = tuple(
            as_non_empty_str(item, "consulta_dgt.normativa[i]")
            for item in normativa_raw
        )

        return cls(
            numero=as_non_empty_str(data["numero"], "consulta_dgt.numero"),
            fecha_salida=require_iso_date(
                data["fecha_salida"], "consulta_dgt.fecha_salida"
            ),
            fecha_entrada=parse_iso_date(
                data.get("fecha_entrada"), "consulta_dgt.fecha_entrada"
            ),
            impuesto=impuesto,
            asunto=as_non_empty_str(data["asunto"], "consulta_dgt.asunto"),
            cuestion_planteada=as_non_empty_str(
                data["cuestion_planteada"], "consulta_dgt.cuestion_planteada"
            ),
            contestacion_completa=as_non_empty_str(
                data["contestacion_completa"],
                "consulta_dgt.contestacion_completa",
            ),
            criterio=as_optional_str(
                data.get("criterio"), "consulta_dgt.criterio"
            ),
            criterio_confidence=confidence,
            normativa=normativa,
            url=as_optional_str(data.get("url"), "consulta_dgt.url"),
            content_hash=content_hash,
            last_fetched_at=require_iso_date(
                data["last_fetched_at"], "consulta_dgt.last_fetched_at"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "numero": self.numero,
            "fecha_salida": self.fecha_salida.isoformat(),
            "impuesto": self.impuesto.value,
            "asunto": self.asunto,
            "cuestion_planteada": self.cuestion_planteada,
            "contestacion_completa": self.contestacion_completa,
            "criterio_confidence": self.criterio_confidence.value,
            "normativa": list(self.normativa),
            "content_hash": self.content_hash,
            "last_fetched_at": self.last_fetched_at.isoformat(),
        }
        if self.fecha_entrada is not None:
            out["fecha_entrada"] = self.fecha_entrada.isoformat()
        if self.criterio is not None:
            out["criterio"] = self.criterio
        if self.url is not None:
            out["url"] = self.url
        return out
