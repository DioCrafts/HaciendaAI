"""Modelo de Resolución TEAC/TEAR.

Los Tribunales Económico-Administrativos (TEAC central + TEARs regionales
+ TEALs locales) son órganos de revisión en VÍA ADMINISTRATIVA, no
jurisdiccional. Sus resoluciones tienen relevancia distinta según el tipo:

- **TEAC unificación de criterio** (art. 242 LGT): **vinculan a la AEAT
  y a los TEAR**. Son la doctrina administrativa de máximo rango.
- **TEAC extensión de efectos** (art. 244 LGT): vinculan a la AEAT en
  supuestos análogos. Indicador fuerte de criterio.
- **TEAC resolución ordinaria**: criterio del TEAC en un caso concreto,
  no vinculante para futuros casos pero muy citada.
- **TEAR**: resuelven en primera instancia. NO vinculan ni TEAC ni AEAT
  fuera del caso resuelto, pero sí indican criterio regional.

El LLM debe distinguir estos cuatro niveles al citar — la respuesta
fiscal cambia mucho si el criterio del TEAC unifica o solo se aplica al
caso concreto.

Identificador canónico: número de reclamación. El formato más común es
`00/<NNNNN>/<año>` (TEAC central) o variantes con sufijos
`/<sec>/<sub>`. Ejemplo: `00/00345/2024`. Algunos TEAR usan
`<TEAR>/<NNNNN>/<año>` (ej. `28/12345/2024` para Madrid).

Campos críticos para no alucinar al citar:

- `numero`: identificador estable.
- `organo` + `sede`: TEAC vs TEAR (regional/local), indispensable para
  saber qué peso doctrinal tiene la resolución.
- `tipo`: si unifica criterio, extiende efectos u ordinaria.
- `sentido`: estimatoria/parcial/desestimatoria/inadmisión/retroacción.
- `impuesto`: tributo afectado (reutiliza `Impuesto` de DGT).
- `criterio` + `criterio_confidence`: extracto del criterio doctrinal +
  flag de validación humana (reutiliza patrón de DGT/jurisprudencia).
- `normativa`: artículos citados, para invalidar el corpus cuando la
  norma cambie.
- `content_hash`: SHA-256 del texto normalizado.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Any, TypeVar

from ._common import (
    ValidationError,
    as_non_empty_str,
    as_optional_str,
    require_iso_date,
    require_keys,
    validate_content_hash,
)
from .consulta_dgt import CriterioConfidence, Impuesto


class OrganoTEA(str, Enum):
    """Tipo de Tribunal Económico-Administrativo.

    `TEAC` es el central (único, en Madrid). `TEAR` agrupa los regionales
    (uno por CCAA). `TEAL` cubre los locales (Madrid y Barcelona ciudad).
    """

    TEAC = "teac"
    TEAR = "tear"
    TEAL = "teal"


class TipoResolucion(str, Enum):
    """Vinculación doctrinal de la resolución.

    `UNIFICA_CRITERIO`: art. 242 LGT, vincula a AEAT y a todos los TEAR.
    `EXTIENDE_EFECTOS`: art. 244 LGT, vincula a la AEAT en supuestos
    análogos.
    `ORDINARIA`: resuelve un caso concreto sin efectos doctrinales
    generales.
    `DESCONOCIDA`: el extractor no pudo determinar el tipo —
    explícitamente honesto, mejor que asumir.
    """

    UNIFICA_CRITERIO = "unifica_criterio"
    EXTIENDE_EFECTOS = "extiende_efectos"
    ORDINARIA = "ordinaria"
    DESCONOCIDA = "desconocida"


class SentidoResolucion(str, Enum):
    """Sentido normalizado del fallo de la resolución."""

    ESTIMATORIA = "estimatoria"
    ESTIMATORIA_PARCIAL = "estimatoria_parcial"
    DESESTIMATORIA = "desestimatoria"
    INADMISION = "inadmision"
    RETROACCION = "retroaccion"
    ARCHIVO = "archivo"
    DESCONOCIDO = "desconocido"


@dataclass(frozen=True)
class ResolucionTEAC:
    """Resolución TEAC/TEAR con metadatos suficientes para citarla."""

    numero: str
    organo: OrganoTEA
    sede: str | None
    fecha: date
    tipo: TipoResolucion
    sentido: SentidoResolucion
    impuesto: Impuesto
    asunto: str
    criterio: str | None
    criterio_confidence: CriterioConfidence
    normativa: tuple[str, ...]
    resolucion_texto: str
    url: str | None
    content_hash: str
    last_fetched_at: date

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ResolucionTEAC":
        require_keys(
            data,
            [
                "numero",
                "organo",
                "fecha",
                "tipo",
                "sentido",
                "impuesto",
                "asunto",
                "criterio_confidence",
                "resolucion_texto",
                "content_hash",
                "last_fetched_at",
            ],
            "resolucion_teac",
        )

        organo = _coerce_enum(data["organo"], OrganoTEA, "resolucion_teac.organo")
        tipo = _coerce_enum(
            data["tipo"], TipoResolucion, "resolucion_teac.tipo"
        )
        sentido = _coerce_enum(
            data["sentido"], SentidoResolucion, "resolucion_teac.sentido"
        )
        impuesto = _coerce_enum(
            data["impuesto"], Impuesto, "resolucion_teac.impuesto"
        )
        confidence = _coerce_enum(
            data["criterio_confidence"],
            CriterioConfidence,
            "resolucion_teac.criterio_confidence",
        )

        content_hash = validate_content_hash(
            as_non_empty_str(data["content_hash"], "resolucion_teac.content_hash")
        )
        if content_hash is None:
            raise ValidationError("resolucion_teac.content_hash es obligatorio")

        normativa_raw = data.get("normativa", [])
        if not isinstance(normativa_raw, list):
            raise ValidationError(
                "resolucion_teac.normativa debe ser lista de strings"
            )
        normativa = tuple(
            as_non_empty_str(item, "resolucion_teac.normativa[i]")
            for item in normativa_raw
        )

        return cls(
            numero=as_non_empty_str(data["numero"], "resolucion_teac.numero"),
            organo=organo,
            sede=as_optional_str(data.get("sede"), "resolucion_teac.sede"),
            fecha=require_iso_date(data["fecha"], "resolucion_teac.fecha"),
            tipo=tipo,
            sentido=sentido,
            impuesto=impuesto,
            asunto=as_non_empty_str(data["asunto"], "resolucion_teac.asunto"),
            criterio=as_optional_str(
                data.get("criterio"), "resolucion_teac.criterio"
            ),
            criterio_confidence=confidence,
            normativa=normativa,
            resolucion_texto=as_non_empty_str(
                data["resolucion_texto"], "resolucion_teac.resolucion_texto"
            ),
            url=as_optional_str(data.get("url"), "resolucion_teac.url"),
            content_hash=content_hash,
            last_fetched_at=require_iso_date(
                data["last_fetched_at"], "resolucion_teac.last_fetched_at"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "numero": self.numero,
            "organo": self.organo.value,
            "fecha": self.fecha.isoformat(),
            "tipo": self.tipo.value,
            "sentido": self.sentido.value,
            "impuesto": self.impuesto.value,
            "asunto": self.asunto,
            "criterio_confidence": self.criterio_confidence.value,
            "normativa": list(self.normativa),
            "resolucion_texto": self.resolucion_texto,
            "content_hash": self.content_hash,
            "last_fetched_at": self.last_fetched_at.isoformat(),
        }
        if self.sede is not None:
            out["sede"] = self.sede
        if self.criterio is not None:
            out["criterio"] = self.criterio
        if self.url is not None:
            out["url"] = self.url
        return out


_E = TypeVar("_E", bound=Enum)


def _coerce_enum(raw_value: Any, enum_cls: type[_E], field_name: str) -> _E:
    """Helper común: parsea string a enum lanzando `ValidationError` con detalle.

    Tipado genérico con `TypeVar` para que mypy preserve el enum
    concreto a la salida y permita iterar sobre los miembros del enum
    para construir el mensaje de error.
    """
    raw = as_non_empty_str(raw_value, field_name)
    try:
        return enum_cls(raw)
    except ValueError as exc:
        allowed = ", ".join(sorted(item.value for item in enum_cls))
        raise ValidationError(
            f"{field_name} '{raw}' no soportado; admitidos: {allowed}"
        ) from exc
