"""Pipeline de ingesta automática de normas fiscales desde el BOE.

Flujo de alto nivel (orquestado por `run_ingestion_for_date`):

    fecha → BoeClient.fetch_summary → parse_summary → [SummaryItem]
          → tax_filter.classify     → [(SummaryItem, Classification)] (fiscal)
          → BoeClient.fetch_document_xml + hash_document → content_hash
          → norma_builder.build_norma → [BuiltNorma]
          → persistence.persist_built_normas → JSON en data/normas/

Cada paso es testable de forma aislada. `run_ingestion_for_date` se cubre
con un test de integración que mockea solo la capa HTTP (`BoeClient`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .boe_client import BoeClient, BoeFetchError, BoeNotFoundError
from .boe_document import DocumentHashError, hash_document
from .boe_summary import SummaryItem, SummaryParseError, parse_summary
from .norma_builder import BuiltNorma, build_norma, kind_to_label
from .persistence import PersistResult, persist_built_normas
from .tax_filter import Classification, classify

__all__ = [
    "BoeClient",
    "BoeFetchError",
    "BoeNotFoundError",
    "BuiltNorma",
    "Classification",
    "DocumentHashError",
    "IngestionReport",
    "PersistResult",
    "SummaryItem",
    "SummaryParseError",
    "build_norma",
    "classify",
    "hash_document",
    "kind_to_label",
    "parse_summary",
    "persist_built_normas",
    "run_ingestion_for_date",
]


@dataclass
class IngestionReport:
    """Resultado de una ingesta diaria, listo para auditoría y para el body del PR.

    `accepted` y `rejected` listan los items del sumario y su clasificación;
    el caller puede serializarlo a JSON estructurado para artefactos del
    workflow.
    """

    target_date: date
    total_summary_items: int
    accepted: list[tuple[SummaryItem, Classification]] = field(default_factory=list)
    rejected: list[tuple[SummaryItem, Classification]] = field(default_factory=list)
    built: list[BuiltNorma] = field(default_factory=list)
    fetch_errors: list[tuple[str, str]] = field(default_factory=list)  # (boe_id, error)
    persist_results: list[PersistResult] = field(default_factory=list)
    no_publication: bool = False

    @property
    def added_count(self) -> int:
        return sum(len(r.added) for r in self.persist_results)

    @property
    def duplicate_count(self) -> int:
        return sum(len(r.duplicates) for r in self.persist_results)

    @property
    def conflict_count(self) -> int:
        return sum(len(r.conflicts) for r in self.persist_results)


def run_ingestion_for_date(
    target: date,
    *,
    client: BoeClient,
    normas_dir: Path,
    dry_run: bool = False,
) -> IngestionReport:
    """Ejecuta el pipeline completo para una fecha.

    Si el sumario devuelve 404 (domingo/festivo sin BOE), devuelve un
    `IngestionReport` con `no_publication=True` y el resto vacío — el cron
    lo trata como ejecución exitosa sin cambios.

    Si `dry_run=True`, ejecuta todo el pipeline pero no escribe a disco.
    Útil para `--dry-run` en CLI y para previsualizar cambios sin tocar
    el corpus.
    """
    report = IngestionReport(target_date=target, total_summary_items=0)

    try:
        payload, content_type = client.fetch_summary(target)
    except BoeNotFoundError:
        report.no_publication = True
        return report

    items = parse_summary(payload, content_type=content_type)
    report.total_summary_items = len(items)

    for item in items:
        classification = classify(
            departamento=item.departamento,
            epigrafe=item.epigrafe,
            titulo=item.titulo,
        )
        if not classification.accept or classification.kind is None:
            report.rejected.append((item, classification))
            continue
        report.accepted.append((item, classification))

        try:
            xml = client.fetch_document_xml(item.boe_id)
            content_hash, _ = hash_document(xml)
        except (BoeFetchError, DocumentHashError) as exc:
            report.fetch_errors.append((item.boe_id, str(exc)))
            continue

        report.built.append(
            build_norma(item, classification=classification, content_hash=content_hash)
        )

    if not dry_run and report.built:
        report.persist_results = persist_built_normas(
            report.built, normas_dir=normas_dir
        )
    return report
