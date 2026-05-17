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

`compute_article_version_drift` (más abajo) extiende esta lógica al
**timeline completo por artículo**: en lugar de comparar el hash de la
versión vigente en una fecha, compara los conjuntos de versiones por
artículo. Detecta versiones añadidas (nueva redacción tras una reforma),
versiones eliminadas (raro: corrección editorial BOE) y versiones
modificadas (mismo `(article_id, effective_from)` con texto distinto —
indica corrección oficial del texto histórico, casi siempre un fix
tipográfico pero merece revisión humana).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal

from ...models import VersionArticulo
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


# ---------- Drift por timeline completo de artículos ----------


ArticleVersionDriftKind = Literal[
    "added",      # versión nueva en el timeline (reforma)
    "removed",    # versión que estaba y ya no está (correción editorial)
    "rewritten",  # mismo (article_id, effective_from) con texto distinto
    "shifted",    # mismo (article_id, effective_from) pero cambio en
                  # effective_to (cierre de versión activa anteriormente)
]


@dataclass(frozen=True)
class ArticleVersionDrift:
    """Una divergencia detectada en el timeline de un artículo concreto.

    `key = (article_id, effective_from)` identifica una versión en el
    timeline. Las tres `kind`s capturan los tipos de cambio que el BOE
    consolidado realmente produce:

    - `added`: hay una versión con `(article_id, effective_from)` nuevo.
      Lo típico tras un RDL que reforma un artículo.
    - `removed`: una versión que registramos antes ya no aparece. Suele
      ser una corrección editorial del BOE (versión publicada por
      error). Conviene investigar manualmente.
    - `rewritten`: la versión tiene la misma `effective_from` pero el
      texto cambió. Es un fix tipográfico oficial; aún así dispara
      drift porque rompe la línea base del corpus.
    - `shifted`: `effective_to` cambió (típicamente de `None` a una
      fecha, porque se modificó posteriormente y BOE cierra la versión
      vigente).
    """

    article_id: str
    effective_from: date
    kind: ArticleVersionDriftKind
    previous: VersionArticulo | None
    current: VersionArticulo | None


@dataclass(frozen=True)
class ArticleVersionDriftReport:
    """Resumen de comparar dos timelines completos por artículo.

    `is_bootstrap=True` indica que `previous` estaba vacío: el caller
    debe persistir el nuevo timeline sin notificar. Mismo criterio que
    `NormaDriftReport`."""

    boe_id: str
    is_bootstrap: bool
    has_changes: bool
    added: tuple[ArticleVersionDrift, ...]
    removed: tuple[ArticleVersionDrift, ...]
    rewritten: tuple[ArticleVersionDrift, ...]
    shifted: tuple[ArticleVersionDrift, ...]

    @property
    def all_changes(self) -> tuple[ArticleVersionDrift, ...]:
        return self.added + self.removed + self.rewritten + self.shifted

    @property
    def affected_article_ids(self) -> tuple[str, ...]:
        return tuple(sorted({d.article_id for d in self.all_changes}))


def compute_article_version_drift(
    *,
    boe_id: str,
    previous_versions: list[VersionArticulo],
    current_versions: list[VersionArticulo],
) -> ArticleVersionDriftReport:
    """Compara dos timelines de `VersionArticulo` por artículo.

    La clave de comparación es `(article_id, effective_from)`: dos
    versiones con la misma clave en `previous` y `current` se
    consideran "la misma versión" y se comparan por `text_hash` (para
    `rewritten`) y `effective_to` (para `shifted`). Versiones con
    claves distintas son `added` o `removed`.

    NO comparamos por `text_hash` puro porque una versión nueva con
    texto idéntico a una anterior es semánticamente una versión nueva
    (BOE re-publica el mismo texto en una norma posterior cuando una
    derogación es luego revertida, por ejemplo).
    """
    if not previous_versions:
        return ArticleVersionDriftReport(
            boe_id=boe_id,
            is_bootstrap=True,
            has_changes=False,
            added=(),
            removed=(),
            rewritten=(),
            shifted=(),
        )

    prev_by_key: dict[tuple[str, date], VersionArticulo] = {
        (v.article_id, v.effective_from): v for v in previous_versions
    }
    curr_by_key: dict[tuple[str, date], VersionArticulo] = {
        (v.article_id, v.effective_from): v for v in current_versions
    }

    added: list[ArticleVersionDrift] = []
    removed: list[ArticleVersionDrift] = []
    rewritten: list[ArticleVersionDrift] = []
    shifted: list[ArticleVersionDrift] = []

    for key in sorted(set(curr_by_key) - set(prev_by_key)):
        article_id, effective_from = key
        added.append(
            ArticleVersionDrift(
                article_id=article_id,
                effective_from=effective_from,
                kind="added",
                previous=None,
                current=curr_by_key[key],
            )
        )
    for key in sorted(set(prev_by_key) - set(curr_by_key)):
        article_id, effective_from = key
        removed.append(
            ArticleVersionDrift(
                article_id=article_id,
                effective_from=effective_from,
                kind="removed",
                previous=prev_by_key[key],
                current=None,
            )
        )
    for key in sorted(set(prev_by_key) & set(curr_by_key)):
        prev_v = prev_by_key[key]
        curr_v = curr_by_key[key]
        if prev_v.text_hash != curr_v.text_hash:
            rewritten.append(
                ArticleVersionDrift(
                    article_id=key[0],
                    effective_from=key[1],
                    kind="rewritten",
                    previous=prev_v,
                    current=curr_v,
                )
            )
        elif prev_v.effective_to != curr_v.effective_to:
            # Texto idéntico, pero `effective_to` cambió: típicamente
            # una versión que estaba abierta (None) ahora se ha
            # cerrado (otra norma la sustituye).
            shifted.append(
                ArticleVersionDrift(
                    article_id=key[0],
                    effective_from=key[1],
                    kind="shifted",
                    previous=prev_v,
                    current=curr_v,
                )
            )

    return ArticleVersionDriftReport(
        boe_id=boe_id,
        is_bootstrap=False,
        has_changes=bool(added or removed or rewritten or shifted),
        added=tuple(added),
        removed=tuple(removed),
        rewritten=tuple(rewritten),
        shifted=tuple(shifted),
    )
