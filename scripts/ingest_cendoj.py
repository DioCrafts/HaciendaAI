"""Ingesta de jurisprudencia tributaria desde CENDOJ.

Modos:

    # Lista de ECLIs explícita
    python scripts/ingest_cendoj.py --ecli ECLI:ES:TS:2024:1234 \\
        --ecli ECLI:ES:AN:2024:567

    # Lista desde fichero (una ECLI por línea, líneas vacías o # ignoradas)
    python scripts/ingest_cendoj.py --ecli-file eclis.txt

    # Cliente local (lee fixtures/archivos previamente descargados)
    python scripts/ingest_cendoj.py --ecli ECLI:ES:TS:2024:1234 \\
        --client local --local-dir path/to/cendoj_html/

    # Modo experimental contra el buscador del CGPJ (rate-limit 3s)
    python scripts/ingest_cendoj.py --ecli ECLI:ES:TS:2024:1234 \\
        --client http

    # Dry-run: clasifica y parsea pero no escribe a disco
    python scripts/ingest_cendoj.py --ecli-file eclis.txt --dry-run

Códigos de salida:
    0 — ejecución correcta (con o sin sentencias aceptadas).
    1 — uno o más ECLIs fallaron en fetch/parse/construcción.
    2 — error fatal de configuración (cliente no disponible, args inválidos).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from hacienda_ai.rag.jurisprudence import (  # noqa: E402
    HttpCendojClient,
    LocalCendojClient,
    run_ingest_for_eclis,
)
from hacienda_ai.rag.jurisprudence.runner import organo_breakdown  # noqa: E402
from hacienda_ai.rag.vector import (  # noqa: E402
    IngestIndexConfigError,
    build_provider_from_args,
    build_store_from_args,
    build_vector_store_args,
    index_sentencias,
)

DEFAULT_ROOT_DIR = REPO_ROOT / "src" / "hacienda_ai" / "data" / "jurisprudencia"
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache" / "cendoj"


def _read_ecli_file(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        ln.strip()
        for ln in lines
        if ln.strip() and not ln.strip().startswith("#")
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingesta CENDOJ → data/jurisprudencia/.")
    parser.add_argument(
        "--ecli",
        action="append",
        default=[],
        help="ECLI a ingerir. Puede repetirse.",
    )
    parser.add_argument(
        "--ecli-file",
        type=Path,
        default=None,
        help="Fichero con una ECLI por línea (# para comentarios).",
    )
    parser.add_argument(
        "--client",
        choices=("local", "http"),
        default="local",
        help="Origen de los HTML: local (--local-dir) o http (cliente experimental).",
    )
    parser.add_argument("--local-dir", type=Path, default=None)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Si se indica, escribe un JSON con el reporte estructurado.",
    )
    build_vector_store_args(parser)
    args = parser.parse_args(argv)

    eclis: list[str] = list(args.ecli)
    if args.ecli_file is not None:
        try:
            eclis.extend(_read_ecli_file(args.ecli_file))
        except OSError as exc:
            print(f"ERROR leyendo {args.ecli_file}: {exc}", file=sys.stderr)
            return 2

    if not eclis:
        print("ERROR: sin ECLIs. Usa --ecli o --ecli-file.", file=sys.stderr)
        return 2

    if args.client == "local":
        if args.local_dir is None:
            print(
                "ERROR: --client local requiere --local-dir.",
                file=sys.stderr,
            )
            return 2
        client: object = LocalCendojClient(root_dir=args.local_dir)
    else:
        client = HttpCendojClient(cache_dir=args.cache)

    today = date.today()
    report = run_ingest_for_eclis(
        eclis,
        client=client,  # type: ignore[arg-type]
        root_dir=args.root,
        today=today,
        persist=not args.dry_run,
    )

    # Resumen por consola.
    for outcome in report.outcomes:
        if outcome.error:
            print(f"  ✗ {outcome.ecli}: {outcome.error}")
        elif outcome.rejected:
            assert outcome.classification is not None
            print(
                f"  · {outcome.ecli} rechazada "
                f"({outcome.classification.relevance})"
            )
        elif outcome.accepted:
            assert outcome.sentencia is not None
            persisted = outcome.persisted
            status = (
                "nueva"
                if persisted is not None and persisted.was_new
                else ("ya existente" if persisted is not None else "dry-run")
            )
            print(
                f"  + {outcome.ecli} "
                f"({outcome.sentencia.organo.value.upper()}, "
                f"fallo={outcome.sentencia.fallo_sentido.value}) {status}"
            )

    breakdown = organo_breakdown(report)
    print(
        f"\nResumen: aceptadas={len(report.accepted)} "
        f"rechazadas={len(report.rejected)} "
        f"errores={len(report.errored)} "
        f"nuevas={len(report.newly_persisted)} | "
        + " ".join(f"{k}={v}" for k, v in breakdown.items() if v > 0)
    )

    index_errors: list[str] = []
    if args.index:
        accepted_sentencias = [
            o.sentencia for o in report.accepted if o.sentencia is not None
        ]
        if not accepted_sentencias:
            print(
                "  · --index: sin sentencias aceptadas; nada que indexar."
            )
        else:
            try:
                provider = build_provider_from_args(args)
                store = build_store_from_args(args)
            except IngestIndexConfigError as exc:
                print(
                    f"ERROR --index: {exc}",
                    file=sys.stderr,
                )
                return 2
            print(
                f"Indexando {len(accepted_sentencias)} sentencias en "
                f"colección {args.collection!r} (provider={args.provider} "
                f"store={args.store})..."
            )
            try:
                index_report = index_sentencias(
                    accepted_sentencias,
                    collection=args.collection,
                    provider=provider,
                    store=store,
                    batch_size=args.index_batch_size,
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"ERROR indexando: {exc}. Los items siguen "
                    "persistidos en disco; reintenta con "
                    "scripts/index_vector_store.py.",
                    file=sys.stderr,
                )
                return 1
            print(
                f"  + indexed total={index_report.total_chunks} "
                f"upserted={index_report.upserted} "
                f"errores={len(index_report.errors)}"
            )
            for err in index_report.errors[:10]:
                print(f"    ✗ {err}", file=sys.stderr)
            index_errors = list(index_report.errors)

    if args.report is not None:
        payload = {
            "today": today.isoformat(),
            "totals": {
                "accepted": len(report.accepted),
                "rejected": len(report.rejected),
                "errored": len(report.errored),
                "newly_persisted": len(report.newly_persisted),
                "index_errors": len(index_errors),
            },
            "by_organo": breakdown,
            "outcomes": [
                {
                    "ecli": o.ecli,
                    "error": o.error,
                    "rejected": o.rejected,
                    "accepted": o.accepted,
                    "newly_persisted": (
                        o.persisted.was_new if o.persisted is not None else False
                    ),
                    "ratio_confidence": (
                        o.sentencia.ratio_confidence.value
                        if o.sentencia is not None
                        else None
                    ),
                    "fallo_sentido": (
                        o.sentencia.fallo_sentido.value
                        if o.sentencia is not None
                        else None
                    ),
                }
                for o in report.outcomes
            ],
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Reporte JSON escrito en {args.report}")

    if report.errored:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
