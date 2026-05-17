"""Ingesta del sumario BOE: detecta normativa fiscal y la registra en `data/normas/`.

Modos:

    python scripts/ingest_boe.py                       # ayer (UTC)
    python scripts/ingest_boe.py --date 2026-05-15
    python scripts/ingest_boe.py --from 2026-05-01 --to 2026-05-15
    python scripts/ingest_boe.py --date 2026-05-15 --dry-run

`--dry-run` ejecuta clasificación, descarga y hashing pero NO escribe a
`data/normas/`. Imprime el reporte por stdout para validar el filtro y
el clasificador antes de mergear PRs reales.

`--report PATH` emite un JSON estructurado con accepted/rejected/built/added
para que el workflow lo use como body del PR.

Códigos de salida:
    0 — ejecución correcta (con o sin cambios; incluye días sin publicación).
    1 — uno o más documentos fallaron al descargarse/hashearse (parcial).
    2 — error fatal (parse del sumario, red caída, args inválidos).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from hacienda_ai.rag.ingestion import (  # noqa: E402
    BoeClient,
    BoeFetchError,
    IngestionReport,
    SummaryParseError,
    kind_to_label,
    run_ingestion_for_date,
)

DEFAULT_NORMAS_DIR = REPO_ROOT / "src" / "hacienda_ai" / "data" / "normas"
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache" / "boe"


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"fecha inválida {raw!r} (esperado YYYY-MM-DD)"
        ) from exc


def _iter_dates(start: date, end: date):
    cur = start
    while cur <= end:
        yield cur
        cur += timedelta(days=1)


def _print_report(report: IngestionReport, *, dry_run: bool) -> None:
    d = report.target_date.isoformat()
    if report.no_publication:
        print(f"[{d}] BOE no publica ese día (404). Sin cambios.")
        return
    print(
        f"[{d}] sumario={report.total_summary_items} "
        f"aceptados={len(report.accepted)} "
        f"rechazados={len(report.rejected)} "
        f"construidos={len(report.built)} "
        f"errores_fetch={len(report.fetch_errors)}"
    )
    for item, classification in report.accepted:
        kind_label = (
            kind_to_label(classification.kind) if classification.kind else "?"
        )
        kw = (
            f" [{', '.join(classification.matched_keywords)}]"
            if classification.matched_keywords
            else ""
        )
        print(f"  + {item.boe_id} ({classification.relevance}, {kind_label}){kw}")
        print(f"     {item.titulo[:140]}")
    for boe_id, error in report.fetch_errors:
        print(f"  ✗ {boe_id}: {error}")
    if dry_run:
        print(f"  (dry-run) {len(report.built)} normas listas para persistir")
    else:
        for result in report.persist_results:
            print(
                f"  → {result.path.name}: añadidas={len(result.added)} "
                f"duplicadas={len(result.duplicates)} conflictos={len(result.conflicts)}"
            )
            for boe_id in result.conflicts:
                print(f"     ⚠ conflicto de hash: {boe_id}")


def _serialize_report(report: IngestionReport) -> dict[str, object]:
    return {
        "target_date": report.target_date.isoformat(),
        "no_publication": report.no_publication,
        "total_summary_items": report.total_summary_items,
        "accepted": [
            {
                "boe_id": item.boe_id,
                "titulo": item.titulo,
                "departamento": item.departamento,
                "epigrafe": item.epigrafe,
                "relevance": cls.relevance,
                "kind": cls.kind.value if cls.kind is not None else None,
                "matched_keywords": list(cls.matched_keywords),
            }
            for item, cls in report.accepted
        ],
        "rejected_count": len(report.rejected),
        "fetch_errors": [
            {"boe_id": boe_id, "error": err} for boe_id, err in report.fetch_errors
        ],
        "persist_results": [
            {
                "path": str(r.path),
                "added": list(r.added),
                "duplicates": list(r.duplicates),
                "conflicts": list(r.conflicts),
            }
            for r in report.persist_results
        ],
        "added_count": report.added_count,
        "duplicate_count": report.duplicate_count,
        "conflict_count": report.conflict_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingesta diaria del sumario BOE → data/normas/.",
    )
    parser.add_argument(
        "--date",
        type=_parse_date,
        default=None,
        help="Fecha a ingerir (YYYY-MM-DD). Por defecto, ayer UTC.",
    )
    parser.add_argument("--from", dest="date_from", type=_parse_date, default=None)
    parser.add_argument("--to", dest="date_to", type=_parse_date, default=None)
    parser.add_argument("--normas-dir", type=Path, default=DEFAULT_NORMAS_DIR)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSON consolidado con accepted/built/added por fecha (para el workflow).",
    )
    args = parser.parse_args(argv)

    if args.date and (args.date_from or args.date_to):
        print("ERROR: --date no compatible con --from/--to", file=sys.stderr)
        return 2
    if (args.date_from is None) != (args.date_to is None):
        print("ERROR: --from y --to deben usarse juntos", file=sys.stderr)
        return 2

    if args.date:
        dates = [args.date]
    elif args.date_from and args.date_to:
        if args.date_to < args.date_from:
            print("ERROR: --to anterior a --from", file=sys.stderr)
            return 2
        dates = list(_iter_dates(args.date_from, args.date_to))
    else:
        # Ayer UTC: el cron corre al alba; el sumario del día actual aún
        # no está cerrado, así que pedimos el del día anterior.
        yesterday = (datetime.now(tz=timezone.utc) - timedelta(days=1)).date()
        dates = [yesterday]

    client = BoeClient(cache_dir=args.cache)
    all_reports: list[IngestionReport] = []
    had_fetch_errors = False
    had_fatal_error = False

    for target in dates:
        try:
            report = run_ingestion_for_date(
                target,
                client=client,
                normas_dir=args.normas_dir,
                dry_run=args.dry_run,
            )
        except SummaryParseError as exc:
            print(f"[{target}] ERROR fatal parseando sumario: {exc}", file=sys.stderr)
            had_fatal_error = True
            continue
        except BoeFetchError as exc:
            print(f"[{target}] ERROR fatal de red: {exc}", file=sys.stderr)
            had_fatal_error = True
            continue
        all_reports.append(report)
        _print_report(report, dry_run=args.dry_run)
        if report.fetch_errors:
            had_fetch_errors = True

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(
                {"runs": [_serialize_report(r) for r in all_reports]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\nReporte JSON escrito en {args.report}")

    if had_fatal_error:
        return 2
    if had_fetch_errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
