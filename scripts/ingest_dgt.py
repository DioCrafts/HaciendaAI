"""Ingesta de consultas vinculantes de la DGT.

Modos:

    # Lista de números explícitos
    python scripts/ingest_dgt.py --numero V0123-24 --numero V0456-24

    # Lista desde fichero (uno por línea; # para comentarios)
    python scripts/ingest_dgt.py --numero-file numeros.txt

    # Cliente local (lee fixtures/archivos previamente descargados)
    python scripts/ingest_dgt.py --numero V0123-24 \\
        --client local --local-dir path/to/dgt_html/

    # Modo experimental contra Petete (rate-limit 3 s)
    python scripts/ingest_dgt.py --numero V0123-24 --client http

    # Dry-run: parsea y construye pero no escribe a disco
    python scripts/ingest_dgt.py --numero-file numeros.txt --dry-run

Códigos de salida:
    0 — ejecución correcta (con o sin consultas nuevas).
    1 — uno o más números fallaron en fetch/parse/build.
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

from hacienda_ai.rag.dgt import (  # noqa: E402
    HttpDgtClient,
    LocalDgtClient,
    impuesto_breakdown,
    run_ingest_for_numeros,
)

DEFAULT_ROOT_DIR = REPO_ROOT / "src" / "hacienda_ai" / "data" / "dgt_consultas"
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache" / "dgt"


def _read_numero_file(path: Path) -> list[str]:
    return [
        ln.strip()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingesta DGT → data/dgt_consultas/.")
    parser.add_argument(
        "--numero",
        action="append",
        default=[],
        help="Número de consulta (V0123-24). Puede repetirse.",
    )
    parser.add_argument("--numero-file", type=Path, default=None)
    parser.add_argument(
        "--client", choices=("local", "http"), default="local"
    )
    parser.add_argument("--local-dir", type=Path, default=None)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)

    numeros: list[str] = list(args.numero)
    if args.numero_file is not None:
        try:
            numeros.extend(_read_numero_file(args.numero_file))
        except OSError as exc:
            print(f"ERROR leyendo {args.numero_file}: {exc}", file=sys.stderr)
            return 2

    if not numeros:
        print("ERROR: sin números. Usa --numero o --numero-file.", file=sys.stderr)
        return 2

    if args.client == "local":
        if args.local_dir is None:
            print("ERROR: --client local requiere --local-dir.", file=sys.stderr)
            return 2
        client: object = LocalDgtClient(root_dir=args.local_dir)
    else:
        client = HttpDgtClient(cache_dir=args.cache)

    today = date.today()
    report = run_ingest_for_numeros(
        numeros,
        client=client,  # type: ignore[arg-type]
        root_dir=args.root,
        today=today,
        persist=not args.dry_run,
    )

    for outcome in report.outcomes:
        if outcome.error:
            print(f"  ✗ {outcome.numero}: {outcome.error}")
        elif outcome.accepted:
            assert outcome.consulta is not None
            persisted = outcome.persisted
            status = (
                "nueva"
                if persisted is not None and persisted.was_new
                else ("ya existente" if persisted is not None else "dry-run")
            )
            print(
                f"  + {outcome.numero} "
                f"({outcome.consulta.impuesto.value.upper()}) "
                f"{status}"
            )

    breakdown = impuesto_breakdown(report)
    print(
        f"\nResumen: aceptadas={len(report.accepted)} "
        f"errores={len(report.errored)} "
        f"nuevas={len(report.newly_persisted)} | "
        + " ".join(f"{k}={v}" for k, v in breakdown.items() if v > 0)
    )

    if args.report is not None:
        payload = {
            "today": today.isoformat(),
            "totals": {
                "accepted": len(report.accepted),
                "errored": len(report.errored),
                "newly_persisted": len(report.newly_persisted),
            },
            "by_impuesto": breakdown,
            "outcomes": [
                {
                    "numero": o.numero,
                    "error": o.error,
                    "accepted": o.accepted,
                    "newly_persisted": (
                        o.persisted.was_new if o.persisted is not None else False
                    ),
                    "impuesto": (
                        o.consulta.impuesto.value
                        if o.consulta is not None
                        else None
                    ),
                    "asunto": (
                        o.consulta.asunto if o.consulta is not None else None
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
