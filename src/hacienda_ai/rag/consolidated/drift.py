"""Diff entre snapshots de una norma: detector de cambios legislativos.

Compara dos huellas `dict[block_id, hash]` (la anterior, persistida, y la
actual, recién calculada) y produce un `NormaDriftReport` con tres listas
mutuamente excluyentes:

- `added`: bloques presentes en el snapshot nuevo pero no en el anterior
  (artículo nuevo introducido por una modificación).
- `removed`: bloques que estaban en el snapshot anterior y desaparecen en
  el nuevo (artículo derogado o renumerado).
- `modified`: bloques presentes en ambos snapshots pero con hash distinto
  (artículo cuyo contenido ha cambiado).

Un bloque renumerado (DT 1ª que pasa a DT 2ª) aparece como `removed` +
`added`, no como `modified`. La revisión humana decide si es renombrado
o reforma sustantiva — la herramienta no infiere eso, solo señala.

El bootstrap (primera ejecución, sin snapshot anterior) NO se considera
drift: devolvemos un report con `has_changes=False` aunque
`added=todos_los_bloques`. El criterio operativo es "¿hay que abrir
issue?", y para una norma que se está incorporando al sistema la
respuesta es no — solo crear el snapshot por primera vez.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from .snapshot import NormaSnapshot

DriftKind = Literal["added", "removed", "modified"]


@dataclass(frozen=True)
class ArticleDrift:
    """Una divergencia detectada en un bloque concreto."""

    block_id: str
    kind: DriftKind
    previous_hash: str | None
    current_hash: str | None


@dataclass(frozen=True)
class NormaDriftReport:
    """Resumen comparativo de dos snapshots de una norma.

    `is_bootstrap=True` indica que no había snapshot previo: el caller
    debe persistir el nuevo snapshot sin notificar drift. `has_changes`
    es `False` también en bootstrap, por la misma razón.
    """

    boe_id: str
    reference_date: date
    is_bootstrap: bool
    has_changes: bool
    added: tuple[ArticleDrift, ...]
    removed: tuple[ArticleDrift, ...]
    modified: tuple[ArticleDrift, ...]
    new_snapshot: NormaSnapshot

    @property
    def all_changes(self) -> tuple[ArticleDrift, ...]:
        return self.added + self.removed + self.modified

    @property
    def affected_block_ids(self) -> tuple[str, ...]:
        return tuple(sorted({d.block_id for d in self.all_changes}))


def compute_norma_drift(
    *,
    boe_id: str,
    reference_date: date,
    current_hashes: dict[str, str],
    previous: NormaSnapshot | None,
    today: date,
) -> NormaDriftReport:
    """Construye el diff entre el snapshot previo y los hashes recién calculados.

    `today` se separa de `reference_date` para tests deterministas: en
    producción se pasa `date.today()`, en tests se inyecta una fecha fija.

    Si `previous is None` se devuelve un bootstrap (no es drift). Si
    `previous` existe, comparamos clave por clave:
    - kw ∈ previous ∩ current con hash distinto → modified
    - kw en current y NO en previous → added
    - kw en previous y NO en current → removed
    """
    new_snapshot = NormaSnapshot(
        boe_id=boe_id,
        last_checked_at=today,
        reference_date=reference_date,
        consolidated_articles=dict(current_hashes),
    )

    if previous is None:
        return NormaDriftReport(
            boe_id=boe_id,
            reference_date=reference_date,
            is_bootstrap=True,
            has_changes=False,
            added=(),
            removed=(),
            modified=(),
            new_snapshot=new_snapshot,
        )

    prev_articles = previous.consolidated_articles
    current_keys = set(current_hashes)
    prev_keys = set(prev_articles)

    added = tuple(
        ArticleDrift(
            block_id=k,
            kind="added",
            previous_hash=None,
            current_hash=current_hashes[k],
        )
        for k in sorted(current_keys - prev_keys)
    )
    removed = tuple(
        ArticleDrift(
            block_id=k,
            kind="removed",
            previous_hash=prev_articles[k],
            current_hash=None,
        )
        for k in sorted(prev_keys - current_keys)
    )
    modified = tuple(
        ArticleDrift(
            block_id=k,
            kind="modified",
            previous_hash=prev_articles[k],
            current_hash=current_hashes[k],
        )
        for k in sorted(prev_keys & current_keys)
        if prev_articles[k] != current_hashes[k]
    )

    return NormaDriftReport(
        boe_id=boe_id,
        reference_date=reference_date,
        is_bootstrap=False,
        has_changes=bool(added or removed or modified),
        added=added,
        removed=removed,
        modified=modified,
        new_snapshot=new_snapshot,
    )
