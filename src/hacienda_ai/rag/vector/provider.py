"""Proveedores de embeddings.

`EmbeddingProvider` Protocol con dos implementaciones por defecto:

- `DeterministicHashEmbeddings`: hashing reproducible, sin red, sin API
  key. NO produce embeddings semánticamente útiles; sirve para CI/tests
  donde lo importante es verificar el pipeline (chunks llegan al store,
  filtros funcionan, etc.), no la calidad del retrieval.

- `VoyageEmbeddings` (en `voyage.py`): HTTP contra api.voyageai.com.

Cualquier proveedor debe exponer:
- `model_id`: string estable que identifica el modelo (`voyage-law-2`).
  Se almacena en cada `EmbeddedChunk` para detectar mezclas de espacios
  vectoriales.
- `dim`: dimensión del vector. Necesario para crear colecciones en
  Qdrant (no se puede cambiar después).
- `embed_documents(texts) -> list[vector]`: embedding de chunks que se
  indexan.
- `embed_query(text) -> vector`: embedding de la consulta. Voyage
  recomienda usar `input_type="query"` aquí (instrucción asimétrica).
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol


class EmbeddingProviderError(RuntimeError):
    """Error al generar embeddings (red, autenticación, formato)."""


class EmbeddingProvider(Protocol):
    """Contrato mínimo de un proveedor de embeddings."""

    model_id: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[tuple[float, ...]]: ...

    def embed_query(self, text: str) -> tuple[float, ...]: ...


# ---------- DeterministicHashEmbeddings ----------


class DeterministicHashEmbeddings:
    """Embedding por hashing reproducible. SOLO para tests / CI / demos.

    Algoritmo:
    1. Tokeniza el texto en palabras (split por whitespace).
    2. Para cada palabra, calcula `SHA-256(palabra)` y proyecta los
       primeros 4 bytes a un índice de dimensión.
    3. Suma 1.0 en ese índice (bolsa-de-palabras-hashed).
    4. Normaliza el vector resultante a norma unitaria (cosine
       similarity en [-1, 1]).

    Propiedades:
    - **Determinista**: mismo texto → mismo vector. Esencial para tests.
    - **Sin red ni API key**.
    - **Inútil semánticamente**: solo captura solapamiento léxico
      crudo. Sirve para verificar el pipeline pero NO la calidad del
      retrieval.

    `dim` por defecto 1024 para coincidir con la dimensión de
    voyage-law-2 — facilita intercambiar proveedores en demos sin
    recrear la colección.
    """

    def __init__(self, *, dim: int = 1024) -> None:
        if dim < 8:
            raise EmbeddingProviderError(
                f"dim demasiado pequeña ({dim}); usa al menos 8"
            )
        self.dim = dim
        self.model_id = f"deterministic-hash-{dim}"

    def embed_documents(self, texts: list[str]) -> list[tuple[float, ...]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._embed(text)

    def _embed(self, text: str) -> tuple[float, ...]:
        vec = [0.0] * self.dim
        for token in text.lower().split():
            if not token:
                continue
            h = hashlib.sha256(token.encode("utf-8")).digest()
            # 4 bytes a entero, módulo `dim`.
            idx = int.from_bytes(h[:4], "big") % self.dim
            vec[idx] += 1.0
        # Normalización L2 para que cosine = dot.
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            # Texto vacío o solo espacios. Devolvemos vector cero —
            # cosine con cualquier otro será 0, lo que es honesto.
            return tuple(vec)
        return tuple(v / norm for v in vec)
