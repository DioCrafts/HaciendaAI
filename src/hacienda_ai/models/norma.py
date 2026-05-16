"""Modelos de Norma y evolución temporal.

Una `Norma` es la identidad estable de un texto legal (Ley, RD, Orden...).
Una `VersionNorma` es un snapshot temporal de esa norma: cuándo entró en vigor
esa redacción, hasta cuándo estuvo viva, y en qué estado quedó (vigente,
derogada, suspendida, declarada inconstitucional).

Se separa la identidad de la versión para poder responder con precisión a
preguntas como "¿qué decía el art. 81 LIRPF en marzo de 2023?" incluso cuando
hoy esa redacción ha sido modificada o derogada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


class SourceKind(str, Enum):
    """Jerarquía normativa de la fuente citada.

    Permite ordenar por rango (ley > reglamento > doctrina administrativa >
    jurisprudencia) y distinguir doctrina administrativa de pronunciamientos
    jurisdiccionales al presentar la respuesta al usuario.
    """

    LEY_ORGANICA = "ley_organica"
    LEY = "ley"
    REAL_DECRETO_LEGISLATIVO = "real_decreto_legislativo"
    REAL_DECRETO = "real_decreto"
    ORDEN_MINISTERIAL = "orden_ministerial"
    DGT_VINCULANTE = "dgt_vinculante"
    TEAC = "teac"
    TS = "ts"
    AN = "an"
    TSJ = "tsj"
    MANUAL_AEAT = "manual_aeat"
    INSTRUCCION_AEAT = "instruccion_aeat"
    PENDIENTE_VALIDACION = "pendiente_validacion"


class NormaStatus(str, Enum):
    VIGENTE = "vigente"
    DEROGADA = "derogada"
    SUSPENDIDA = "suspendida"
    INCONSTITUCIONAL = "inconstitucional"


@dataclass(frozen=True)
class Norma:
    """Identidad estable de una norma legal."""

    boe_id: str
    kind: SourceKind
    title: str
    enacted_at: date

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Norma":
        require_keys(data, ["boe_id", "kind", "title", "enacted_at"], "norma")
        kind_raw = as_non_empty_str(data["kind"], "norma.kind")
        try:
            kind = SourceKind(kind_raw)
        except ValueError as exc:
            allowed = ", ".join(sorted(item.value for item in SourceKind))
            raise ValidationError(
                f"norma.kind '{kind_raw}' no soportado; valores admitidos: {allowed}"
            ) from exc
        return cls(
            boe_id=as_non_empty_str(data["boe_id"], "norma.boe_id"),
            kind=kind,
            title=as_non_empty_str(data["title"], "norma.title"),
            enacted_at=require_iso_date(data["enacted_at"], "norma.enacted_at"),
        )


@dataclass(frozen=True)
class VersionNorma:
    """Snapshot temporal de una norma.

    Referencia a `Norma.boe_id` con vigencia explícita y estado del periodo.
    """

    norma_boe_id: str
    effective_from: date
    status: NormaStatus
    effective_to: date | None = None
    content_hash: str | None = None
    modified_by_boe_id: str | None = None
    notes: str | None = None

    def __post_init__(self) -> None:
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValidationError(
                f"VersionNorma de {self.norma_boe_id}: effective_to "
                f"({self.effective_to.isoformat()}) anterior a effective_from "
                f"({self.effective_from.isoformat()})"
            )

    def covers(self, target: date) -> bool:
        """True si esta versión cubre `target` (sin importar su status)."""
        if target < self.effective_from:
            return False
        if self.effective_to is not None and target > self.effective_to:
            return False
        return True

    def is_active_on(self, target: date) -> bool:
        """True solo si cubre `target` y su status es VIGENTE."""
        return self.covers(target) and self.status == NormaStatus.VIGENTE

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VersionNorma":
        require_keys(
            data,
            ["norma_boe_id", "effective_from", "status"],
            "version_norma",
        )
        status_raw = as_non_empty_str(data["status"], "version_norma.status")
        try:
            status = NormaStatus(status_raw)
        except ValueError as exc:
            allowed = ", ".join(sorted(item.value for item in NormaStatus))
            raise ValidationError(
                f"version_norma.status '{status_raw}' no soportado; valores admitidos: {allowed}"
            ) from exc
        return cls(
            norma_boe_id=as_non_empty_str(
                data["norma_boe_id"], "version_norma.norma_boe_id"
            ),
            effective_from=require_iso_date(
                data["effective_from"], "version_norma.effective_from"
            ),
            effective_to=parse_iso_date(
                data.get("effective_to"), "version_norma.effective_to"
            ),
            status=status,
            content_hash=validate_content_hash(
                as_optional_str(data.get("content_hash"), "version_norma.content_hash")
            ),
            modified_by_boe_id=as_optional_str(
                data.get("modified_by_boe_id"), "version_norma.modified_by_boe_id"
            ),
            notes=as_optional_str(data.get("notes"), "version_norma.notes"),
        )


@dataclass
class NormaRegistry:
    """Catálogo en memoria de Normas y sus VersionNormas.

    Permite responder: "¿qué versión de la norma X estaba viva en la fecha Y,
    y en qué estado?". Garantiza unicidad de identidad y ausencia de
    solapamientos temporales entre versiones de la misma norma.
    """

    _normas: dict[str, Norma] = field(default_factory=dict)
    _versions: dict[str, list[VersionNorma]] = field(default_factory=dict)

    def register_norma(self, norma: Norma) -> None:
        existing = self._normas.get(norma.boe_id)
        if existing is not None and existing != norma:
            raise ValidationError(
                f"Norma {norma.boe_id} ya registrada con metadatos distintos"
            )
        self._normas[norma.boe_id] = norma

    def register_version(self, version: VersionNorma) -> None:
        bucket = self._versions.setdefault(version.norma_boe_id, [])
        for existing in bucket:
            if self._overlap(existing, version):
                raise ValidationError(
                    f"Solapamiento de versiones en {version.norma_boe_id}: "
                    f"[{existing.effective_from}, {existing.effective_to}] vs "
                    f"[{version.effective_from}, {version.effective_to}]"
                )
        bucket.append(version)
        bucket.sort(key=lambda v: v.effective_from)

    def get_norma(self, boe_id: str) -> Norma | None:
        return self._normas.get(boe_id)

    def versions_for(self, boe_id: str) -> list[VersionNorma]:
        return list(self._versions.get(boe_id, ()))

    def version_at(self, boe_id: str, target: date) -> VersionNorma | None:
        """Devuelve la versión que cubre `target` para esa norma, si existe."""
        for version in self._versions.get(boe_id, ()):
            if version.covers(target):
                return version
        return None

    def status_at(self, boe_id: str, target: date) -> NormaStatus | None:
        version = self.version_at(boe_id, target)
        return version.status if version is not None else None

    def knows(self, boe_id: str) -> bool:
        return boe_id in self._normas

    @staticmethod
    def _overlap(a: VersionNorma, b: VersionNorma) -> bool:
        a_end = a.effective_to or date.max
        b_end = b.effective_to or date.max
        return a.effective_from <= b_end and b.effective_from <= a_end

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NormaRegistry":
        registry = cls()
        for raw in data.get("normas", []):
            registry.register_norma(Norma.from_dict(raw))
        for raw in data.get("versions", []):
            version = VersionNorma.from_dict(raw)
            if not registry.knows(version.norma_boe_id):
                raise ValidationError(
                    f"VersionNorma referencia norma '{version.norma_boe_id}' no registrada"
                )
            registry.register_version(version)
        return registry
