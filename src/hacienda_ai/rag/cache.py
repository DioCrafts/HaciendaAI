"""Invalidación de caches del RAG ante cambios legislativos detectados.

El RAG (retrieval-augmented generation) aún no está construido en este
repo — `rag/retrieval/` está vacío. Pero la detección de drift YA
necesita decidir qué hay que recalcular cuando el BOE modifica un
artículo: chunks indexados, embeddings cacheados, índices BM25, re-ranks
precomputados.

Para no atar el detector a una implementación concreta de vector store,
exponemos una interfaz abstracta `RAGCache` con dos operaciones:

- `invalidate(boe_id, articles, reason)`: marca obsoletos los recursos
  asociados a (boe_id, article).
- `recent_invalidations(...)`: devuelve el log para auditoría.

Implementación inicial `JsonAuditLog`: append-only sobre
`data/rag_cache_invalidations.json`. NO toca el vector store (no existe
todavía); registra qué hay que reindexar cuando exista. El fichero es
commitable: el diff de un PR muestra exactamente qué se invalidó.

Cuando el vector store real se integre, se añade una implementación
`QdrantCache` (u otra) que además llame al backend para borrar puntos.
El detector de drift no necesitará cambios — solo se le pasa otra
instancia de `RAGCache`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Invalidation:
    """Registro inmutable de una invalidación realizada."""

    timestamp_utc: str
    boe_id: str
    articles: tuple[str, ...]
    reason: str

    def to_json(self) -> dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc,
            "boe_id": self.boe_id,
            "articles": list(self.articles),
            "reason": self.reason,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Invalidation":
        articles_raw = data.get("articles", [])
        if not isinstance(articles_raw, list):
            raise ValueError("articles debe ser lista")
        return cls(
            timestamp_utc=str(data["timestamp_utc"]),
            boe_id=str(data["boe_id"]),
            articles=tuple(str(a) for a in articles_raw),
            reason=str(data.get("reason", "")),
        )


class RAGCache(Protocol):
    """Contrato mínimo de un backend de cache RAG."""

    def invalidate(
        self, *, boe_id: str, articles: list[str], reason: str
    ) -> Invalidation: ...

    def recent_invalidations(self, *, limit: int = 100) -> list[Invalidation]: ...


@dataclass
class JsonAuditLog:
    """Implementación append-only sobre fichero JSON.

    Estructura del fichero:
        {
          "invalidations": [
            {"timestamp_utc": "...", "boe_id": "...", "articles": [...], "reason": "..."},
            ...
          ]
        }

    Pensada para producción mientras no hay vector store: el detector de
    drift llama a `invalidate(...)` y el log queda en el repo para que un
    operador (o el script de reindex futuro) sepa qué materializar de
    nuevo.

    `_clock` es inyectable para timestamps deterministas en tests.
    """

    path: Path
    _clock: Any = field(default=None)  # Callable[[], datetime] | None

    def invalidate(
        self, *, boe_id: str, articles: list[str], reason: str
    ) -> Invalidation:
        timestamp = (
            self._clock() if self._clock is not None else datetime.now(tz=timezone.utc)
        )
        entry = Invalidation(
            timestamp_utc=timestamp.isoformat(timespec="seconds"),
            boe_id=boe_id,
            # Deduplicamos y ordenamos para diffs estables y para no
            # inflar el log con repeticiones de la misma ejecución.
            articles=tuple(sorted(set(articles))),
            reason=reason,
        )
        self._append(entry)
        return entry

    def recent_invalidations(self, *, limit: int = 100) -> list[Invalidation]:
        data = self._load()
        entries = data.get("invalidations", [])
        # Devolvemos los `limit` más recientes en orden cronológico
        # descendente (último primero) — útil para mostrar en logs/UI.
        return [Invalidation.from_json(e) for e in entries[-limit:]][::-1]

    def all_for(self, boe_id: str) -> list[Invalidation]:
        """Todas las invalidaciones registradas para una norma."""
        return [
            inv
            for inv in self.recent_invalidations(limit=10**9)
            if inv.boe_id == boe_id
        ]

    # ---------- Internals ----------

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"invalidations": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{self.path}: log corrupto: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{self.path}: raíz del log no es objeto")
        data.setdefault("invalidations", [])
        if not isinstance(data["invalidations"], list):
            raise ValueError(f"{self.path}: 'invalidations' debe ser lista")
        return data

    def _append(self, entry: Invalidation) -> None:
        data = self._load()
        data["invalidations"].append(entry.to_json())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(self.path)
