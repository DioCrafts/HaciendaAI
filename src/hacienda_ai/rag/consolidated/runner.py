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

from ...models import NormaRegistry, NormaStatus
from ..cache import RAGCache
from .articles import all_block_hashes
from .drift import NormaDriftReport, compute_norma_drift
from .fetcher import ConsolidatedFetcher, ConsolidatedFetchError
from .snapshot import (
    SnapshotError,
    load_snapshot,
    save_snapshot,
    snapshot_path,
)


@dataclass(frozen=True)
class NormaCheckOutcome:
    """Resultado de comprobar UNA norma del registry."""

    boe_id: str
    drift: NormaDriftReport | None
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
) -> NormaCheckOutcome:
    """Ejecuta el chequeo de UNA norma. No filtra elegibilidad (lo hace el caller).

    Si detecta drift y `persist=True`:
    - Invalida las entradas RAG de los bloques afectados.
    - Pide al fetcher que invalide su cache local (próximo `fetch` baja XML fresco).
    - Persiste el nuevo snapshot.
    En bootstrap solo persiste el snapshot.
    """
    try:
        xml = fetcher.fetch(boe_id)
    except ConsolidatedFetchError as exc:
        return NormaCheckOutcome(
            boe_id=boe_id, drift=None, skipped_reason=None, error=str(exc)
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

    return NormaCheckOutcome(
        boe_id=boe_id, drift=drift, skipped_reason=None, error=None
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
) -> CheckRunReport:
    """Recorre todas las normas del registry y devuelve un reporte agregado.

    El orden es lexicográfico por `boe_id` para diffs estables del
    reporte serializado.
    """
    report = CheckRunReport(reference_date=reference_date, today=today)
    for boe_id in registry.all_boe_ids():
        eligible, reason = _is_eligible(registry, boe_id, today)
        if not eligible:
            report.outcomes.append(
                NormaCheckOutcome(
                    boe_id=boe_id, drift=None, skipped_reason=reason, error=None
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
        )
        report.outcomes.append(outcome)
        if persist and outcome.drift is not None:
            report.snapshots_written.append(snapshot_path(snapshots_dir, boe_id))
    return report


def serialize_report(report: CheckRunReport) -> dict[str, object]:
    """Convierte el reporte a JSON-friendly para el body del PR/issue."""

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

    return {
        "reference_date": report.reference_date.isoformat(),
        "today": report.today.isoformat(),
        "outcomes": [
            {
                "boe_id": o.boe_id,
                "drift": _drift_to_dict(o.drift),
                "skipped_reason": o.skipped_reason,
                "error": o.error,
            }
            for o in report.outcomes
        ],
        "summary": {
            "drift_count": len(report.drift_outcomes),
            "bootstrap_count": len(report.bootstrap_outcomes),
            "skipped_count": len(report.skipped),
            "error_count": len(report.errored),
        },
    }


