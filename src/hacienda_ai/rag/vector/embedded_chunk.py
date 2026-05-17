"""Tipos del pipeline de vectorización: IndexableChunk, EmbeddedChunk, queries.

`IndexableChunk` es la pieza neutra a la que se mapean TODOS los modelos
del corpus (`Norma`/`Sentencia`/`ConsultaDGT`/`ResolucionTEAC`/
`ManualChunk`) antes de embeber. Eso desacopla:

- El RAG no necesita conocer los modelos de dominio.
- Si añadimos un nuevo tipo de fuente (manuales IS, doctrina europea…)
  solo creamos un `iter_<X>_chunks` que produzca `IndexableChunk`; el
  resto del pipeline lo absorbe.

`metadata` es libre pero seguimos una convención mínima para que los
filtros del retrieval funcionen sin sorpresas:

- `fecha`: fecha del documento (ISO YYYY-MM-DD) cuando aplica.
- `impuesto`: valor del enum `Impuesto` (irpf/iva/is/…) cuando aplica.
- `effective_from` / `effective_to`: vigencia, para filtro temporal.
- `tribunal_codigo`, `organo`, `tipo_resolucion`, etc.: específicos por
  tipo de fuente; documentados en `corpus.py`.

Los filtros del query se traducen a la API del backend concreto
(Qdrant `must`, InMemory predicate). Si el backend no soporta un filtro,
se aplica post-search en memoria.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any


class SourceType(str, Enum):
    """Categoría del documento de origen.

    Permite filtrar el retrieval por familia ("solo jurisprudencia",
    "solo consultas DGT") y razonar sobre el peso doctrinal de cada
    resultado en la respuesta del LLM.
    """

    NORMA = "norma"
    SENTENCIA = "sentencia"
    CONSULTA_DGT = "consulta_dgt"
    RESOLUCION_TEAC = "resolucion_teac"
    MANUAL = "manual"


@dataclass(frozen=True)
class IndexableChunk:
    """Unidad indexable, agnóstica del modelo de dominio.

    `chunk_id` debe ser único en todo el corpus. La convención:
    `<source_type>::<id_específico_del_modelo>` (ej.
    `sentencia::ECLI:ES:TS:2024:1234`, `consulta_dgt::V0123-24`,
    `manual::manual_irpf::2024::cap1::sec1_1::sub1_1_1::p1of1`).
    """

    chunk_id: str
    source_type: SourceType
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddedChunk:
    """`IndexableChunk` con su vector embebido y modelo de origen.

    `embedding_model` es la firma del modelo que produjo el vector
    (`voyage-law-2`, `deterministic-hash-1024`…). Almacenarla permite
    reindexar selectivamente cuando cambia el modelo, y detectar mezclas
    accidentales de espacios vectoriales distintos.
    """

    chunk_id: str
    source_type: SourceType
    text: str
    embedding: tuple[float, ...]
    embedding_model: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def vector_dim(self) -> int:
        return len(self.embedding)


@dataclass(frozen=True)
class VectorQuery:
    """Consulta semántica al store.

    `text` se embebe en tiempo de ejecución con el mismo proveedor que
    indexó el corpus (es responsabilidad del caller asegurarlo).

    Filtros opcionales:
    - `source_types`: limita a familias concretas.
    - `impuesto`: limita por figura tributaria.
    - `fecha_devengo`: filtro temporal — solo recupera chunks cuya
      vigencia (`effective_from <= fecha_devengo <= effective_to`)
      cubre esa fecha. Sin filtros temporales, una respuesta puede
      citar normativa derogada.
    """

    text: str
    top_k: int = 10
    source_types: tuple[SourceType, ...] | None = None
    impuesto: str | None = None
    fecha_devengo: date | None = None
    min_score: float = 0.0


@dataclass(frozen=True)
class VectorMatch:
    """Resultado del retrieval: chunk + score de similitud.

    `score` es la similitud (cosine, dot, …) devuelta por el backend.
    En Qdrant con vectores normalizados es cosine ∈ [-1, 1].
    """

    chunk: EmbeddedChunk
    score: float
