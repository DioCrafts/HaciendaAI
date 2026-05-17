"""Contrato del retriever híbrido visto desde la capa de chat.

El orquestador (`orchestrator.py`) y la tool `retrieve_legal_context`
(`tools.py`) comparten un mismo contrato mínimo: un método
`search(VectorQuery) -> list[VectorMatch]`. Dejamos el Protocol en su
propio módulo para evitar un ciclo `orchestrator.py` ↔ `tools.py`.

En producción `rag.hybrid.HybridRetriever` cumple este Protocol; en
tests inyectamos stubs deterministas. La capa de chat queda así
desacoplada del backend de retrieval (Qdrant + Voyage en producción,
InMemory + hash en CI) — el orquestador no sabe ni necesita saber
qué hay detrás.
"""

from __future__ import annotations

from typing import Protocol

from ..rag.vector import VectorMatch, VectorQuery


class LegalContextRetriever(Protocol):
    """Búsqueda híbrida sobre el corpus legal.

    El contrato es deliberadamente estrecho — solo `search` —. Cualquier
    otra capacidad del backend (índices, batching, cacheo) es interna
    al retriever inyectado y no la observa el chat.
    """

    def search(self, query: VectorQuery) -> list[VectorMatch]: ...


__all__ = ["LegalContextRetriever"]
