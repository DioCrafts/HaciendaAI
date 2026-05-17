"""Modelo de Sentencia para corpus de jurisprudencia tributaria.

Una `Sentencia` es un acto jurisdiccional firme citable en una respuesta
fiscal. A diferencia de una `Norma`, no tiene versiones temporales: su
texto es definitivo desde la fecha de publicación. Sí puede ser superada
por jurisprudencia posterior (cambio de doctrina), pero eso es metadato
externo, no una modificación del texto.

Identificador canónico: ECLI (European Case Law Identifier, formato
`ECLI:ES:<tribunal>:<año>:<id>`). Es el único id estable garantizado
para sentencias publicadas en CENDOJ.

Campos críticos para no alucinar al citar jurisprudencia:

- `ecli`: identificador canónico verificable contra CENDOJ.
- `organo` + `tribunal_codigo`: el órgano (TS/AN/TSJ/AP) genérico
  necesario para razonar sobre jerarquía + el código ECLI exacto del
  tribunal concreto (TS, TSJM, APB…) que es lo que aparece en la URL
  pública.
- `sala`, `seccion`: imprescindibles para distinguir doctrina
  contencioso-administrativa (Sala 3ª TS) de social (Sala 4ª TS).
- `fecha`: fecha de la sentencia (no de publicación CENDOJ).
- `ponente`: para auditar la doctrina cuando un cambio responde a
  rotación de magistrados.
- `fallo_sentido` + `fallo_texto`: el sentido normalizado
  (estimatoria/desestimatoria/…) + el texto literal del bloque FALLO.
- `ratio_decidendi`: extracto del fundamento jurídico decisivo, con
  `ratio_confidence` que distingue extracción automática heurística de
  validación humana.
- `content_hash`: SHA-256 del texto normalizado completo, para detectar
  si CENDOJ corrigió la sentencia (raro pero posible).
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


class Organo(str, Enum):
    """Órgano jurisdiccional genérico, suficiente para razonar sobre jerarquía.

    El detalle (qué TS concreto, qué TSJ provincial) va en `tribunal_codigo`.
    """

    TS = "ts"
    AN = "an"
    TSJ = "tsj"
    AP = "ap"
    TC = "tc"  # Tribunal Constitucional


class FalloSentido(str, Enum):
    """Sentido normalizado del fallo.

    `DESCONOCIDO` cuando el extractor heurístico no pudo determinar el
    sentido — es honesto, mejor que asumir un valor erróneo.
    """

    ESTIMATORIA = "estimatoria"
    ESTIMATORIA_PARCIAL = "estimatoria_parcial"
    DESESTIMATORIA = "desestimatoria"
    INADMISION = "inadmision"
    CASACION = "casacion"  # casa la sentencia recurrida.
    NULIDAD = "nulidad"
    DESCONOCIDO = "desconocido"


class RatioConfidence(str, Enum):
    """Nivel de confianza del extractor de ratio decidendi.

    `AUTO` significa "heurística sin validación humana"; el LLM debe
    citarla con cautela ("según extracto automático, …"). `MANUAL`
    significa que un revisor humano leyó la sentencia y editó el campo
    a mano — citable como doctrina.
    """

    AUTO = "auto"
    MANUAL = "manual"


@dataclass(frozen=True)
class Sentencia:
    """Acto jurisdiccional con metadatos suficientes para citarlo sin alucinar."""

    ecli: str
    organo: Organo
    tribunal_codigo: str  # Código ECLI exacto: "TS", "TSJM", "APB", "AN"…
    sala: str | None
    seccion: str | None
    fecha: date
    ponente: str | None
    numero_resolucion: str | None
    numero_recurso: str | None
    fallo_sentido: FalloSentido
    fallo_texto: str
    ratio_decidendi: str | None
    ratio_confidence: RatioConfidence
    resumen: str | None
    url: str | None
    content_hash: str
    last_fetched_at: date

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Sentencia":
        require_keys(
            data,
            [
                "ecli",
                "organo",
                "tribunal_codigo",
                "fecha",
                "fallo_sentido",
                "fallo_texto",
                "ratio_confidence",
                "content_hash",
                "last_fetched_at",
            ],
            "sentencia",
        )
        organo_raw = as_non_empty_str(data["organo"], "sentencia.organo")
        try:
            organo = Organo(organo_raw)
        except ValueError as exc:
            allowed = ", ".join(sorted(o.value for o in Organo))
            raise ValidationError(
                f"sentencia.organo '{organo_raw}' no soportado; admitidos: {allowed}"
            ) from exc

        sentido_raw = as_non_empty_str(
            data["fallo_sentido"], "sentencia.fallo_sentido"
        )
        try:
            sentido = FalloSentido(sentido_raw)
        except ValueError as exc:
            allowed = ", ".join(sorted(s.value for s in FalloSentido))
            raise ValidationError(
                f"sentencia.fallo_sentido '{sentido_raw}' no soportado; admitidos: {allowed}"
            ) from exc

        conf_raw = as_non_empty_str(
            data["ratio_confidence"], "sentencia.ratio_confidence"
        )
        try:
            confidence = RatioConfidence(conf_raw)
        except ValueError as exc:
            allowed = ", ".join(sorted(c.value for c in RatioConfidence))
            raise ValidationError(
                f"sentencia.ratio_confidence '{conf_raw}' no soportado; admitidos: {allowed}"
            ) from exc

        content_hash = validate_content_hash(
            as_non_empty_str(data["content_hash"], "sentencia.content_hash")
        )
        if content_hash is None:
            raise ValidationError("sentencia.content_hash es obligatorio")

        return cls(
            ecli=as_non_empty_str(data["ecli"], "sentencia.ecli"),
            organo=organo,
            tribunal_codigo=as_non_empty_str(
                data["tribunal_codigo"], "sentencia.tribunal_codigo"
            ),
            sala=as_optional_str(data.get("sala"), "sentencia.sala"),
            seccion=as_optional_str(data.get("seccion"), "sentencia.seccion"),
            fecha=require_iso_date(data["fecha"], "sentencia.fecha"),
            ponente=as_optional_str(data.get("ponente"), "sentencia.ponente"),
            numero_resolucion=as_optional_str(
                data.get("numero_resolucion"), "sentencia.numero_resolucion"
            ),
            numero_recurso=as_optional_str(
                data.get("numero_recurso"), "sentencia.numero_recurso"
            ),
            fallo_sentido=sentido,
            fallo_texto=as_non_empty_str(
                data["fallo_texto"], "sentencia.fallo_texto"
            ),
            ratio_decidendi=as_optional_str(
                data.get("ratio_decidendi"), "sentencia.ratio_decidendi"
            ),
            ratio_confidence=confidence,
            resumen=as_optional_str(data.get("resumen"), "sentencia.resumen"),
            url=as_optional_str(data.get("url"), "sentencia.url"),
            content_hash=content_hash,
            last_fetched_at=require_iso_date(
                data["last_fetched_at"], "sentencia.last_fetched_at"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "ecli": self.ecli,
            "organo": self.organo.value,
            "tribunal_codigo": self.tribunal_codigo,
            "fecha": self.fecha.isoformat(),
            "fallo_sentido": self.fallo_sentido.value,
            "fallo_texto": self.fallo_texto,
            "ratio_confidence": self.ratio_confidence.value,
            "content_hash": self.content_hash,
            "last_fetched_at": self.last_fetched_at.isoformat(),
        }
        # Campos opcionales solo si tienen valor: mantenemos JSON limpio.
        if self.sala is not None:
            out["sala"] = self.sala
        if self.seccion is not None:
            out["seccion"] = self.seccion
        if self.ponente is not None:
            out["ponente"] = self.ponente
        if self.numero_resolucion is not None:
            out["numero_resolucion"] = self.numero_resolucion
        if self.numero_recurso is not None:
            out["numero_recurso"] = self.numero_recurso
        if self.ratio_decidendi is not None:
            out["ratio_decidendi"] = self.ratio_decidendi
        if self.resumen is not None:
            out["resumen"] = self.resumen
        if self.url is not None:
            out["url"] = self.url
        return out


def parse_iso_date_for_sentencia(value: Any, field_name: str) -> date | None:
    """Reexport vía nombre legible; útil para tests."""
    return parse_iso_date(value, field_name)
