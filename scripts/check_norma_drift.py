"""Detecta cambios legislativos en el consolidado BOE de las normas registradas.

Para cada `Norma` con `boe_id` estatal y versión vigente hoy:

1. Descarga el XML consolidado (con cache TTL = 1 día).
2. Hashea cada bloque precepto en su versión vigente.
3. Compara contra `data/normas/snapshots/<boe_id>.json` de la última pasada.
4. Si hay drift:
   - Añade entrada al log de invalidación RAG.
   - Borra la cache del XML consolidado de esa norma (próximo `fetch` baja fresco).
   - Reescribe el snapshot con la huella nueva.
5. Si era bootstrap (sin snapshot previo), crea el snapshot sin notificar.

Modos:

    python scripts/check_norma_drift.py
    python scripts/check_norma_drift.py --reference-date 2024-12-31
    python scripts/check_norma_drift.py --dry-run
    python scripts/check_norma_drift.py --report drift-report.json

Códigos de salida:
    0 — sin drift (incluye bootstrap, sin cambios, todas saltadas).
    1 — drift detectado en al menos una norma.
    2 — error fatal (snapshot corrupto, red caída sobre todas las normas).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from hacienda_ai.normas import load_norma_registry  # noqa: E402
from hacienda_ai.rag.cache import JsonAuditLog  # noqa: E402
from hacienda_ai.rag.consolidated import (  # noqa: E402
    ConsolidatedFetcher,
    run_check_for_registry,
    serialize_report,
)

DEFAULT_NORMAS_DIR = REPO_ROOT / "src" / "hacienda_ai" / "data" / "normas"
DEFAULT_SNAPSHOTS_DIR = (
    REPO_ROOT / "src" / "hacienda_ai" / "data" / "normas" / "snapshots"
)
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache" / "boe" / "consolidated"
DEFAULT_INVALIDATION_LOG = (
    REPO_ROOT / "src" / "hacienda_ai" / "data" / "rag_cache_invalidations.json"
)


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"fecha inválida {raw!r} (esperado YYYY-MM-DD)"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cron de detección de cambios en consolidado BOE."
    )
    parser.add_argument("--normas-dir", type=Path, default=DEFAULT_NORMAS_DIR)
    parser.add_argument(
        "--snapshots-dir", type=Path, default=DEFAULT_SNAPSHOTS_DIR
    )
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--invalidation-log",
        type=Path,
        default=DEFAULT_INVALIDATION_LOG,
        help="Log JSON append-only de invalidaciones RAG.",
    )
    parser.add_argument(
        "--reference-date",
        type=_parse_date,
        default=None,
        help="Fecha objetivo para seleccionar versión vigente (por defecto, hoy).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="No persiste snapshots ni log de invalidación. Sí descarga BOE.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSON con el reporte completo (consumido por el workflow).",
    )
    args = parser.parse_args(argv)

    today = date.today()
    reference_date = args.reference_date or today

    try:
        registry = load_norma_registry(args.normas_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR cargando registry desde {args.normas_dir}: {exc}", file=sys.stderr)
        return 2

    fetcher = ConsolidatedFetcher(cache_dir=args.cache)
    rag_cache = JsonAuditLog(path=args.invalidation_log)

    report = run_check_for_registry(
        registry,
        fetcher=fetcher,
        snapshots_dir=args.snapshots_dir,
        rag_cache=rag_cache,
        reference_date=reference_date,
        today=today,
        persist=not args.dry_run,
    )

    # Resumen por consola.
    for outcome in report.outcomes:
        if outcome.skipped_reason:
            print(f"  · {outcome.boe_id} (skip: {outcome.skipped_reason})")
            continue
        if outcome.error:
            print(f"  ✗ {outcome.boe_id}: {outcome.error}")
            continue
        drift = outcome.drift
        assert drift is not None
        if drift.is_bootstrap:
            print(
                f"  + {outcome.boe_id} bootstrap "
                f"({len(drift.new_snapshot.article_ids)} bloques)"
            )
        elif drift.has_changes:
            print(
                f"  ! {outcome.boe_id} DRIFT: "
                f"+{len(drift.added)} -{len(drift.removed)} ~{len(drift.modified)}"
            )
            for d in drift.modified[:10]:
                print(
                    f"     ~ {d.block_id}: "
                    f"{(d.previous_hash or '')[:16]} → {(d.current_hash or '')[:16]}"
                )
            if len(drift.modified) > 10:
                print(f"     ~ … y {len(drift.modified) - 10} más")
        else:
            print(f"  ✓ {outcome.boe_id} sin cambios")

    print(
        f"\nResumen: drift={len(report.drift_outcomes)} "
        f"bootstrap={len(report.bootstrap_outcomes)} "
        f"skipped={len(report.skipped)} "
        f"errores={len(report.errored)}"
    )

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(serialize_report(report), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        print(f"Reporte JSON escrito en {args.report}")

    # Política de exit code:
    #   2: si TODAS las normas elegibles fallaron (red caída) — sin
    #      ninguna comprobación posible, no es transitorio.
    #   1: drift en al menos una norma.
    #   0: bootstrap, sin cambios, errores parciales con éxito en otras.
    eligible = [
        o for o in report.outcomes if o.skipped_reason is None
    ]
    if eligible and all(o.error is not None for o in eligible):
        return 2
    if report.drift_outcomes:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
