"""Orquestador del cron de detección de cambios legislativos.

Para cada norma estatal del `NormaRegistry`:
1. Descarga (o sirve de caché) el texto consolidado del BOE.
2. Calcula los hashes por bloque vigente a `reference_date`.
3. Compara contra el snapshot persistido en `data/normas/snapshots/`.
4. Si hay drift: invalida cache RAG asociada y deja drift en el reporte.
5. Persiste el nuevo snapshot (bootstrap o tras drift).

Las normas no estatales (boletines autonómicos) se saltan: el API
consolidada de BOE solo cubre normativa estatal. Esas normas se
auditarán cuando exista verificador por boletín (TODO).

Las normas con `status == DEROGADA` o `INCONSTITUCIONAL` en el registry
se saltan también: su contenido consolidado es estático histórico, no
puede haber drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ...models import NormaRegistry, NormaStatus, VersionArticulo
from ..cache import RAGCache
from .article_snapshot import (
    ArticleSnapshotError,
    ArticleVersionSnapshot,
    load_article_snapshot,
    save_article_snapshot,
)
from .articles import all_block_hashes, iter_article_versions
from .drift import (
    ArticleVersionDriftReport,
    NormaDriftReport,
    compute_article_version_drift,
    compute_norma_drift,
)
from .fetcher import ConsolidatedFetcher, ConsolidatedFetchError
from .snapshot import (
    SnapshotError,
    load_snapshot,
    save_snapshot,
    snapshot_path,
)


@dataclass(frozen=True)
class NormaCheckOutcome:
    """Resultado de comprobar UNA norma del registry.

    `drift` es el diff a nivel norma (hashes por bloque vigente en
    `reference_date`). `article_version_drift` es el diff a nivel
    timeline completo (todas las versiones de cada artículo). Ambos se
    calculan en cada ejecución; el primero es barato y suficiente para
    detectar cambios del día, el segundo es la fuente canónica del
    versionado por artículo que alimenta `ArticleRegistry`.
    """

    boe_id: str
    drift: NormaDriftReport | None
    article_version_drift: ArticleVersionDriftReport | None
    skipped_reason: str | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None and self.skipped_reason is None


@dataclass
class CheckRunReport:
    """Resumen de una ejecución completa del cron sobre todas las normas."""

    reference_date: date
    today: date
    outcomes: list[NormaCheckOutcome] = field(default_factory=list)
    snapshots_written: list[Path] = field(default_factory=list)

    @property
    def drift_outcomes(self) -> list[NormaCheckOutcome]:
        return [
            o for o in self.outcomes if o.drift is not None and o.drift.has_changes
        ]

    @property
    def bootstrap_outcomes(self) -> list[NormaCheckOutcome]:
        return [
            o for o in self.outcomes if o.drift is not None and o.drift.is_bootstrap
        ]

    @property
    def errored(self) -> list[NormaCheckOutcome]:
        return [o for o in self.outcomes if o.error is not None]

    @property
    def skipped(self) -> list[NormaCheckOutcome]:
        return [o for o in self.outcomes if o.skipped_reason is not None]


def _is_eligible(registry: NormaRegistry, boe_id: str, today: date) -> tuple[bool, str | None]:
    """Decide si una norma entra al chequeo.

    Saltamos:
    - Normas no estatales (`boe_id` no empieza por "BOE-A-"): sin
      consolidado API.
    - Normas con la versión vigente hoy en estado distinto de VIGENTE
      (derogada, inconstitucional, suspendida): contenido estático o no
      aplicable.
    - Normas sin ninguna versión cubriendo la fecha de hoy: futura o ya
      historial.
    """
    if not boe_id.startswith("BOE-A-"):
        return False, "norma no estatal (sin API consolidada)"
    version = registry.version_at(boe_id, today)
    if version is None:
        return False, f"sin versión en el registry que cubra {today.isoformat()}"
    if version.status != NormaStatus.VIGENTE:
        return False, f"versión en estado {version.status.value}"
    return True, None


def check_norma(
    boe_id: str,
    *,
    fetcher: ConsolidatedFetcher,
    snapshots_dir: Path,
    rag_cache: RAGCache,
    reference_date: date,
    today: date,
    persist: bool = True,
    article_snapshots_dir: Path | None = None,
) -> NormaCheckOutcome:
    """Ejecuta el chequeo de UNA norma. No filtra elegibilidad (lo hace el caller).

    Si detecta drift y `persist=True`:
    - Invalida las entradas RAG de los bloques afectados.
    - Pide al fetcher que invalide su cache local (próximo `fetch` baja XML fresco).
    - Persiste el nuevo snapshot.
    En bootstrap solo persiste el snapshot.

    `article_snapshots_dir`: si se inyecta, además del snapshot de hashes
    a fecha (`snapshots_dir`) calculamos y persistimos el timeline
    completo por artículo en `article_snapshots_dir/<boe_id>.json`. Es
    opcional para no romper código existente que solo necesita el diff
    a fecha; cuando se proporciona, `outcome.article_version_drift` lleva
    el `ArticleVersionDriftReport`. Si está `None` o falla la lectura
    previa, el outcome lleva `article_version_drift=None` y se reporta
    el error sin abortar — el diff por fecha sigue siendo válido por
    sí solo.
    """
    try:
        xml = fetcher.fetch(boe_id)
    except ConsolidatedFetchError as exc:
        return NormaCheckOutcome(
            boe_id=boe_id,
            drift=None,
            article_version_drift=None,
            skipped_reason=None,
            error=str(exc),
        )

    current = all_block_hashes(xml, reference_date)

    try:
        previous = load_snapshot(snapshots_dir, boe_id)
    except SnapshotError as exc:
        # Snapshot corrupto: no podemos comparar. Reportar error y NO
        # sobrescribir para evitar perder la línea base si era reparable.
        return NormaCheckOutcome(
            boe_id=boe_id,
            drift=None,
            article_version_drift=None,
            skipped_reason=None,
            error=f"snapshot corrupto: {exc}",
        )

    drift = compute_norma_drift(
        boe_id=boe_id,
        reference_date=reference_date,
        current_hashes=current,
        previous=previous,
        today=today,
    )

    article_drift: ArticleVersionDriftReport | None = None
    new_article_snapshot: ArticleVersionSnapshot | None = None
    if article_snapshots_dir is not None:
        current_article_versions = list(
            iter_article_versions(xml, norma_boe_id=boe_id)
        )
        try:
            previous_article_snapshot = load_article_snapshot(
                article_snapshots_dir, boe_id
            )
        except ArticleSnapshotError as exc:
            # Mismo criterio que con NormaSnapshot: snapshot corrupto =
            # error explícito, no asumir bootstrap. Pero NO devolvemos
            # aquí: ya tenemos el diff a fecha, devolvémoslo sin el
            # article drift y dejamos el error visible.
            return NormaCheckOutcome(
                boe_id=boe_id,
                drift=drift,
                article_version_drift=None,
                skipped_reason=None,
                error=f"article snapshot corrupto: {exc}",
            )
        prev_versions_list = (
            list(previous_article_snapshot.versions)
            if previous_article_snapshot is not None
            else []
        )
        article_drift = compute_article_version_drift(
            boe_id=boe_id,
            previous_versions=prev_versions_list,
            current_versions=current_article_versions,
        )
        new_article_snapshot = ArticleVersionSnapshot(
            boe_id=boe_id,
            last_checked_at=today,
            reference_date=reference_date,
            versions=tuple(current_article_versions),
        )

    if persist:
        save_snapshot(snapshots_dir, drift.new_snapshot)
        if drift.has_changes:
            # Invalida cache RAG de los bloques afectados Y la cache de
            # XML consolidado local — la próxima ejecución bajará el XML
            # fresco para confirmar el cambio.
            rag_cache.invalidate(
                boe_id=boe_id,
                articles=list(drift.affected_block_ids),
                reason=(
                    f"Drift consolidado detectado el {today.isoformat()}: "
                    f"{len(drift.added)} bloques añadidos, "
                    f"{len(drift.removed)} eliminados, "
                    f"{len(drift.modified)} modificados."
                ),
            )
            fetcher.invalidate(boe_id)
        if (
            article_snapshots_dir is not None
            and new_article_snapshot is not None
        ):
            save_article_snapshot(article_snapshots_dir, new_article_snapshot)

    return NormaCheckOutcome(
        boe_id=boe_id,
        drift=drift,
        article_version_drift=article_drift,
        skipped_reason=None,
        error=None,
    )


def run_check_for_registry(
    registry: NormaRegistry,
    *,
    fetcher: ConsolidatedFetcher,
    snapshots_dir: Path,
    rag_cache: RAGCache,
    reference_date: date,
    today: date,
    persist: bool = True,
    article_snapshots_dir: Path | None = None,
) -> CheckRunReport:
    """Recorre todas las normas del registry y devuelve un reporte agregado.

    El orden es lexicográfico por `boe_id` para diffs estables del
    reporte serializado.

    `article_snapshots_dir`: se reenvía a `check_norma`. Si se inyecta,
    cada chequeo además calcula y persiste el timeline completo por
    artículo (`ArticleVersionSnapshot`) y rellena
    `outcome.article_version_drift`. Si es `None`, comportamiento legacy.
    """
    report = CheckRunReport(reference_date=reference_date, today=today)
    for boe_id in registry.all_boe_ids():
        eligible, reason = _is_eligible(registry, boe_id, today)
        if not eligible:
            report.outcomes.append(
                NormaCheckOutcome(
                    boe_id=boe_id,
                    drift=None,
                    article_version_drift=None,
                    skipped_reason=reason,
                    error=None,
                )
            )
            continue
        outcome = check_norma(
            boe_id,
            fetcher=fetcher,
            snapshots_dir=snapshots_dir,
            rag_cache=rag_cache,
            reference_date=reference_date,
            today=today,
            persist=persist,
            article_snapshots_dir=article_snapshots_dir,
        )
        report.outcomes.append(outcome)
        if persist and outcome.drift is not None:
            report.snapshots_written.append(snapshot_path(snapshots_dir, boe_id))
    return report


def serialize_report(report: CheckRunReport) -> dict[str, object]:
    """Convierte el reporte a JSON-friendly para el body del PR/issue.

    Incluye el diff a fecha (`drift`) y el diff de timeline por artículo
    (`article_version_drift`) cuando esté disponible. El segundo se
    omite del JSON si es `None` para mantener el output compacto en
    runs legacy (sin `article_snapshots_dir`).
    """

    def _drift_to_dict(d: NormaDriftReport | None) -> dict[str, object] | None:
        if d is None:
            return None
        return {
            "is_bootstrap": d.is_bootstrap,
            "has_changes": d.has_changes,
            "added": [
                {"block_id": a.block_id, "current_hash": a.current_hash}
                for a in d.added
            ],
            "removed": [
                {"block_id": a.block_id, "previous_hash": a.previous_hash}
                for a in d.removed
            ],
            "modified": [
                {
                    "block_id": a.block_id,
                    "previous_hash": a.previous_hash,
                    "current_hash": a.current_hash,
                }
                for a in d.modified
            ],
        }

    def _article_drift_to_dict(
        d: ArticleVersionDriftReport | None,
    ) -> dict[str, object] | None:
        if d is None:
            return None

        def _v(ver: VersionArticulo | None) -> dict[str, object] | None:
            if ver is None:
                return None
            return {
                "article_id": ver.article_id,
                "effective_from": ver.effective_from.isoformat(),
                "effective_to": (
                    ver.effective_to.isoformat()
                    if ver.effective_to is not None
                    else None
                ),
                "text_hash": ver.text_hash,
                "modified_by_boe_id": ver.modified_by_boe_id,
            }

        return {
            "is_bootstrap": d.is_bootstrap,
            "has_changes": d.has_changes,
            "added": [
                {
                    "article_id": x.article_id,
                    "effective_from": x.effective_from.isoformat(),
                    "current": _v(x.current),
                }
                for x in d.added
            ],
            "removed": [
                {
                    "article_id": x.article_id,
                    "effective_from": x.effective_from.isoformat(),
                    "previous": _v(x.previous),
                }
                for x in d.removed
            ],
            "rewritten": [
                {
                    "article_id": x.article_id,
                    "effective_from": x.effective_from.isoformat(),
                    "previous": _v(x.previous),
                    "current": _v(x.current),
                }
                for x in d.rewritten
            ],
            "shifted": [
                {
                    "article_id": x.article_id,
                    "effective_from": x.effective_from.isoformat(),
                    "previous": _v(x.previous),
                    "current": _v(x.current),
                }
                for x in d.shifted
            ],
        }

    def _outcome_to_dict(o: NormaCheckOutcome) -> dict[str, object]:
        out: dict[str, object] = {
            "boe_id": o.boe_id,
            "drift": _drift_to_dict(o.drift),
            "skipped_reason": o.skipped_reason,
            "error": o.error,
        }
        if o.article_version_drift is not None:
            out["article_version_drift"] = _article_drift_to_dict(
                o.article_version_drift
            )
        return out

    return {
        "reference_date": report.reference_date.isoformat(),
        "today": report.today.isoformat(),
        "outcomes": [_outcome_to_dict(o) for o in report.outcomes],
        "summary": {
            "drift_count": len(report.drift_outcomes),
            "bootstrap_count": len(report.bootstrap_outcomes),
            "skipped_count": len(report.skipped),
            "error_count": len(report.errored),
        },
    }


