"""Modelo de fragmento (chunk) de manual o publicación AEAT.

La AEAT publica anualmente el **Manual Práctico** de cada gran tributo
(IRPF, IS, IVA…) con la doctrina operativa: cómo se aplican las normas
en la práctica, casos típicos, ejemplos numéricos, criterios
administrativos no codificados en consultas DGT. Además mantiene el
servicio **INFORMA**, un FAQ-base con miles de preguntas-respuestas
operativas.

Un `ManualChunk` es un fragmento semánticamente coherente de uno de
esos materiales, listo para indexar en un RAG. La unidad mínima es la
subsección (o el FAQ en el caso de INFORMA). Cuando una subsección es
demasiado grande para encajar en una ventana de embedding, el chunker
la subdivide preservando la metadata jerárquica.

Campos críticos:

- `chunk_id`: id estable que identifica unívocamente el chunk dentro
  del corpus. Formato:
  `<fuente>::<ejercicio>::<capitulo>::<seccion>::<subseccion>::p<n>`.
  Los segmentos vacíos colapsan a `_`. Permite diff estable entre
  ingesta y reindexación.
- `fuente`: `MANUAL_IRPF`, `MANUAL_IS`, `MANUAL_IVA` o `INFORMA_FAQ`.
- `ejercicio`: año fiscal al que aplica (Manual IRPF 2024 cubre la
  declaración del ejercicio 2024). En INFORMA puede ser `None` cuando
  la pregunta no se vincula a un ejercicio concreto.
- `capitulo` / `seccion` / `subseccion`: jerarquía documental tal como
  aparece en el manual original. INFORMA usa solo `titulo` + `materia`.
- `titulo`: encabezado humano del chunk.
- `contenido`: texto plano del chunk listo para embedding o citación.
- `page_inicio` / `page_fin`: páginas físicas del PDF (manuales).
  Permite citar pinpoint ("Manual IRPF 2024, p. 215").
- `referencias_normativas`: artículos/normas citados en el chunk
  (`Ley 35/2006 art. 19.2.e)`). Útil para invalidar el chunk cuando la
  norma referenciada cambie.
- `url_fuente`: URL canónica del documento de origen, si está
  disponible (página del manual en sede.agenciatributaria.gob.es).
- `content_hash`: SHA-256 del texto normalizado, para detectar cambios
  cuando la AEAT republica el manual.

Diseño deliberado: NO incluimos `criterio_confidence` aquí. El
contenido del manual ES doctrina administrativa publicada — no se
extrae heurísticamente como en jurisprudencia/DGT/TEAC. Si la AEAT lo
dice en su Manual Práctico, es el criterio AEAT verbatim.
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
    require_iso_date,
    require_keys,
    validate_content_hash,
)


class ManualFuente(str, Enum):
    """Identifica el material AEAT origen del chunk.

    Mantenemos los manuales prácticos como entradas distintas porque la
    AEAT publica un volumen distinto por tributo y cada uno tiene
    estructura editorial propia. `INFORMA_FAQ` es el servicio de
    preguntas frecuentes (estructura plana pregunta/respuesta).
    """

    MANUAL_IRPF = "manual_irpf"
    MANUAL_IS = "manual_is"
    MANUAL_IVA = "manual_iva"
    MANUAL_SOCIEDADES = "manual_sociedades"  # alias informal de MANUAL_IS.
    INFORMA_FAQ = "informa_faq"


@dataclass(frozen=True)
class ManualChunk:
    """Fragmento semánticamente coherente de un manual AEAT."""

    chunk_id: str
    fuente: ManualFuente
    ejercicio: int | None
    capitulo: str | None
    seccion: str | None
    subseccion: str | None
    titulo: str
    contenido: str
    page_inicio: int | None
    page_fin: int | None
    referencias_normativas: tuple[str, ...]
    url_fuente: str | None
    content_hash: str
    last_fetched_at: date

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ManualChunk":
        require_keys(
            data,
            [
                "chunk_id",
                "fuente",
                "titulo",
                "contenido",
                "content_hash",
                "last_fetched_at",
            ],
            "manual_chunk",
        )
        fuente_raw = as_non_empty_str(data["fuente"], "manual_chunk.fuente")
        try:
            fuente = ManualFuente(fuente_raw)
        except ValueError as exc:
            allowed = ", ".join(sorted(f.value for f in ManualFuente))
            raise ValidationError(
                f"manual_chunk.fuente '{fuente_raw}' no soportado; admitidos: {allowed}"
            ) from exc

        content_hash = validate_content_hash(
            as_non_empty_str(data["content_hash"], "manual_chunk.content_hash")
        )
        if content_hash is None:
            raise ValidationError("manual_chunk.content_hash es obligatorio")

        refs_raw = data.get("referencias_normativas", [])
        if not isinstance(refs_raw, list):
            raise ValidationError(
                "manual_chunk.referencias_normativas debe ser lista"
            )
        referencias = tuple(
            as_non_empty_str(item, "manual_chunk.referencias_normativas[i]")
            for item in refs_raw
        )

        ejercicio_raw = data.get("ejercicio")
        if ejercicio_raw is not None and not isinstance(ejercicio_raw, int):
            raise ValidationError("manual_chunk.ejercicio debe ser int o null")
        page_inicio_raw = data.get("page_inicio")
        page_fin_raw = data.get("page_fin")
        for label, val in (("page_inicio", page_inicio_raw), ("page_fin", page_fin_raw)):
            if val is not None and not isinstance(val, int):
                raise ValidationError(
                    f"manual_chunk.{label} debe ser int o null"
                )

        return cls(
            chunk_id=as_non_empty_str(data["chunk_id"], "manual_chunk.chunk_id"),
            fuente=fuente,
            ejercicio=ejercicio_raw,
            capitulo=as_optional_str(data.get("capitulo"), "manual_chunk.capitulo"),
            seccion=as_optional_str(data.get("seccion"), "manual_chunk.seccion"),
            subseccion=as_optional_str(
                data.get("subseccion"), "manual_chunk.subseccion"
            ),
            titulo=as_non_empty_str(data["titulo"], "manual_chunk.titulo"),
            contenido=as_non_empty_str(
                data["contenido"], "manual_chunk.contenido"
            ),
            page_inicio=page_inicio_raw,
            page_fin=page_fin_raw,
            referencias_normativas=referencias,
            url_fuente=as_optional_str(
                data.get("url_fuente"), "manual_chunk.url_fuente"
            ),
            content_hash=content_hash,
            last_fetched_at=require_iso_date(
                data["last_fetched_at"], "manual_chunk.last_fetched_at"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            "fuente": self.fuente.value,
            "titulo": self.titulo,
            "contenido": self.contenido,
            "referencias_normativas": list(self.referencias_normativas),
            "content_hash": self.content_hash,
            "last_fetched_at": self.last_fetched_at.isoformat(),
        }
        if self.ejercicio is not None:
            out["ejercicio"] = self.ejercicio
        if self.capitulo is not None:
            out["capitulo"] = self.capitulo
        if self.seccion is not None:
            out["seccion"] = self.seccion
        if self.subseccion is not None:
            out["subseccion"] = self.subseccion
        if self.page_inicio is not None:
            out["page_inicio"] = self.page_inicio
        if self.page_fin is not None:
            out["page_fin"] = self.page_fin
        if self.url_fuente is not None:
            out["url_fuente"] = self.url_fuente
        return out
