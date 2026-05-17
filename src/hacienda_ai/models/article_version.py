"""Versionado a nivel artículo de las normas consolidadas.

`VersionNorma` (en `norma.py`) trabaja a nivel NORMA: marca una ley como
vigente, derogada o suspendida en un intervalo. Es la granularidad
correcta para "¿existe esta ley hoy?" pero **insuficiente** para asesorar
sobre tributos: la LIRPF está marcada `VIGENTE` desde 2007, pero su
art. 23 ha tenido al menos 4 redacciones distintas en ese intervalo. Una
respuesta correcta a "¿qué retención se aplica a alquileres en 2018?"
exige saber qué decía art. 23 LIRPF en 2018, no si la LIRPF como bloque
estaba vigente.

`VersionArticulo` introduce esa granularidad: por cada artículo (o
disposición, DT, DA…) de una norma, mantiene un timeline de redacciones
con `effective_from`, `effective_to`, `modified_by_boe_id` (la norma que
introdujo el cambio) y el texto literal. El parser de textos consolidados
BOE rellena este timeline desde el XML oficial — ver
`rag.consolidated.articles.iter_article_versions`.

Modelo:

    VersionArticulo
        norma_boe_id  ─── ¿de qué norma es?    (BOE-A-2006-20764)
        article_id    ─── ¿qué bloque?         (a23, a81bis, dt6)
        effective_from
        effective_to  (None = vigente sin fecha de fin conocida)
        text          ─── redacción literal normalizada
        text_hash     ─── SHA-256 de `text` para validación rápida
        modified_by_boe_id (None si no se ha podido determinar)

`ArticleRegistry` es el lookup análogo a `NormaRegistry` pero indexado
por `(norma_boe_id, article_id, date)`. La diferencia clave es que aquí
las versiones de un artículo PUEDEN tener `effective_to=None` con
`effective_from` posterior a la primera versión: cuando se modifica un
artículo, BOE cierra la versión anterior (`effective_to` = día previo) y
abre una nueva (`effective_from` = día de entrada en vigor).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Iterable

from ._common import (
    ValidationError,
    as_non_empty_str,
    as_optional_str,
    parse_iso_date,
    require_iso_date,
    require_keys,
    validate_content_hash,
)


@dataclass(frozen=True)
class VersionArticulo:
    """Una redacción concreta de un artículo en un intervalo temporal.

    Inmutable y comparable por valor (`frozen=True`) para que dos
    instancias del mismo (`norma_boe_id`, `article_id`, `effective_from`)
    con texto idéntico se traten como la misma versión, y dos con texto
    distinto generen drift detectable.

    `text` es la forma normalizada del texto (parrafos concatenados sin
    notas al pie editoriales, espacios colapsados). `text_hash` es el
    SHA-256 hex de `text.encode('utf-8')`; el validador rechaza
    incoherencias para evitar persistir un timeline con hashes calculados
    sobre otro texto.
    """

    norma_boe_id: str
    article_id: str
    effective_from: date
    effective_to: date | None
    text: str
    text_hash: str
    modified_by_boe_id: str | None = None

    def __post_init__(self) -> None:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValidationError(
                f"VersionArticulo {self.norma_boe_id}/{self.article_id}: "
                f"effective_to ({self.effective_to.isoformat()}) anterior a "
                f"effective_from ({self.effective_from.isoformat()})"
            )
        if not self.text_hash or len(self.text_hash) != 64:
            raise ValidationError(
                f"VersionArticulo {self.norma_boe_id}/{self.article_id}: "
                "text_hash debe ser SHA-256 hex (64 caracteres)"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", self.text_hash):
            raise ValidationError(
                f"VersionArticulo {self.norma_boe_id}/{self.article_id}: "
                "text_hash debe contener solo dígitos hex en minúsculas"
            )

    def covers(self, target: date) -> bool:
        """True si esta versión cubre `target`. Análogo a
        `VersionNorma.covers`."""
        if target < self.effective_from:
            return False
        if self.effective_to is not None and target > self.effective_to:
            return False
        return True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VersionArticulo":
        require_keys(
            data,
            [
                "norma_boe_id",
                "article_id",
                "effective_from",
                "text",
                "text_hash",
            ],
            "version_articulo",
        )
        text_hash_raw = as_non_empty_str(
            data["text_hash"], "version_articulo.text_hash"
        )
        validated = validate_content_hash(text_hash_raw)
        if validated is None:
            raise ValidationError(
                "version_articulo.text_hash es obligatorio"
            )
        return cls(
            norma_boe_id=as_non_empty_str(
                data["norma_boe_id"], "version_articulo.norma_boe_id"
            ),
            article_id=as_non_empty_str(
                data["article_id"], "version_articulo.article_id"
            ),
            effective_from=require_iso_date(
                data["effective_from"], "version_articulo.effective_from"
            ),
            effective_to=parse_iso_date(
                data.get("effective_to"), "version_articulo.effective_to"
            ),
            text=as_non_empty_str(data["text"], "version_articulo.text"),
            text_hash=validated,
            modified_by_boe_id=as_optional_str(
                data.get("modified_by_boe_id"),
                "version_articulo.modified_by_boe_id",
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "norma_boe_id": self.norma_boe_id,
            "article_id": self.article_id,
            "effective_from": self.effective_from.isoformat(),
            "text": self.text,
            "text_hash": self.text_hash,
        }
        if self.effective_to is not None:
            out["effective_to"] = self.effective_to.isoformat()
        if self.modified_by_boe_id is not None:
            out["modified_by_boe_id"] = self.modified_by_boe_id
        return out


@dataclass
class ArticleRegistry:
    """Catálogo en memoria de `VersionArticulo`s indexado por (norma, artículo).

    Permite resolver "¿qué texto tenía art. 23 LIRPF el 1 de marzo de 2023?"
    en O(versiones_del_articulo) — lineal sobre el timeline del artículo
    concreto, no sobre todo el corpus.

    Invariantes:
    - No solapamientos temporales entre versiones del mismo
      `(norma_boe_id, article_id)`. BOE consolidado nunca produce
      solapamientos legítimos; si llegan, lanzamos `ValidationError`.
    - El timeline de cada artículo se mantiene ordenado por
      `effective_from`.
    """

    _versions: dict[tuple[str, str], list[VersionArticulo]] = field(
        default_factory=dict
    )

    def register(self, version: VersionArticulo) -> None:
        key = (version.norma_boe_id, version.article_id)
        bucket = self._versions.setdefault(key, [])
        for existing in bucket:
            if self._overlap(existing, version):
                raise ValidationError(
                    f"Solapamiento de versiones de artículo "
                    f"{version.norma_boe_id}/{version.article_id}: "
                    f"[{existing.effective_from}, {existing.effective_to}] vs "
                    f"[{version.effective_from}, {version.effective_to}]"
                )
        bucket.append(version)
        bucket.sort(key=lambda v: v.effective_from)

    def register_many(self, versions: Iterable[VersionArticulo]) -> None:
        for v in versions:
            self.register(v)

    def version_at(
        self, norma_boe_id: str, article_id: str, target: date
    ) -> VersionArticulo | None:
        """Devuelve la versión de `article_id` en `norma_boe_id` activa en
        `target`, o `None` si no hay redacción vigente entonces.

        Una respuesta `None` significa una de tres cosas:
        - El artículo no existe (id desconocido).
        - El artículo se introdujo después de `target` (futuro).
        - El artículo fue derogado antes de `target`.
        En todos los casos el caller debe degradar la cita a `warn` —
        afirmar contenido sin redacción vigente es incorrecto.
        """
        for version in self._versions.get((norma_boe_id, article_id), ()):
            if version.covers(target):
                return version
        return None

    def versions_for(
        self, norma_boe_id: str, article_id: str
    ) -> list[VersionArticulo]:
        return list(self._versions.get((norma_boe_id, article_id), ()))

    def knows_article(self, norma_boe_id: str, article_id: str) -> bool:
        return (norma_boe_id, article_id) in self._versions

    def all_articles_for(self, norma_boe_id: str) -> tuple[str, ...]:
        """Devuelve los `article_id` con al menos una versión registrada
        en `norma_boe_id`, ordenados lexicográficamente."""
        return tuple(
            sorted(
                aid
                for (nid, aid) in self._versions
                if nid == norma_boe_id
            )
        )

    def all_norma_boe_ids(self) -> tuple[str, ...]:
        """Devuelve los `norma_boe_id` con al menos un artículo registrado."""
        return tuple(sorted({nid for (nid, _) in self._versions}))

    @property
    def total_versions(self) -> int:
        return sum(len(v) for v in self._versions.values())

    @staticmethod
    def _overlap(a: VersionArticulo, b: VersionArticulo) -> bool:
        a_end = a.effective_to or date.max
        b_end = b.effective_to or date.max
        return a.effective_from <= b_end and b.effective_from <= a_end

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ArticleRegistry":
        """Construye un registry desde un dump `{"versions": [...]}`.

        Útil para tests y para reconstruir el registry desde el snapshot
        persistido en `data/normas/article_versions/`.
        """
        registry = cls()
        raw_versions = data.get("versions", [])
        if not isinstance(raw_versions, list):
            raise ValidationError(
                "article_registry.versions debe ser lista"
            )
        for raw in raw_versions:
            registry.register(VersionArticulo.from_dict(raw))
        return registry

    def to_dict(self) -> dict[str, Any]:
        """Serializa el registry completo. Las versiones se ordenan por
        `(norma_boe_id, article_id, effective_from)` para diffs estables."""
        out: list[dict[str, Any]] = []
        for key in sorted(self._versions):
            for v in self._versions[key]:
                out.append(v.to_dict())
        return {"versions": out}


__all__ = [
    "ArticleRegistry",
    "VersionArticulo",
]
