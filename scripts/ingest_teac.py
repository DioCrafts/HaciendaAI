"""Ingesta de resoluciones TEAC/TEAR/TEAL.

Modos:

    # Lista explícita de números (R.G. o canónico DD/NNNNN/AAAA)
    python scripts/ingest_teac.py --numero 00/12345/2023 --numero "R.G. 67890/2022"

    # Lista desde fichero (uno por línea; # para comentarios)
    python scripts/ingest_teac.py --numero-file numeros.txt

    # Cliente local (fixtures/archivos previamente descargados)
    python scripts/ingest_teac.py --numero 00/12345/2023 \\
        --client local --local-dir path/to/teac_html/

    # Cliente experimental contra DYCTEA (rate-limit 3 s)
    python scripts/ingest_teac.py --numero 00/12345/2023 --client http

    # Dry-run: parsea y construye sin escribir.
    python scripts/ingest_teac.py --numero-file numeros.txt --dry-run

Códigos de salida:
    0 — todas procesadas (con aciertos/errores parciales).
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

from hacienda_ai.rag.teac import (  # noqa: E402
    HttpTeacClient,
    LocalTeacClient,
    impuesto_breakdown,
    run_ingest_for_numeros,
    tipo_breakdown,
)

DEFAULT_ROOT_DIR = REPO_ROOT / "src" / "hacienda_ai" / "data" / "teac_resoluciones"
DEFAULT_CACHE_DIR = REPO_ROOT / ".cache" / "teac"


def _read_numero_file(path: Path) -> list[str]:
    return [
        ln.strip()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingesta TEAC/TEAR → data/teac_resoluciones/."
    )
    parser.add_argument(
        "--numero",
        action="append",
        default=[],
        help="Número de reclamación (R.G., 00/12345/2023, etc.). Puede repetirse.",
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
        client: object = LocalTeacClient(root_dir=args.local_dir)
    else:
        client = HttpTeacClient(cache_dir=args.cache)

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
            assert outcome.resolucion is not None
            persisted = outcome.persisted
            status = (
                "nueva"
                if persisted is not None and persisted.was_new
                else ("ya existente" if persisted is not None else "dry-run")
            )
            print(
                f"  + {outcome.numero} "
                f"({outcome.resolucion.organo.value.upper()}, "
                f"tipo={outcome.resolucion.tipo.value}, "
                f"sentido={outcome.resolucion.sentido.value}, "
                f"impuesto={outcome.resolucion.impuesto.value.upper()}) "
                f"{status}"
            )

    by_tipo = tipo_breakdown(report)
    by_imp = impuesto_breakdown(report)
    print(
        f"\nResumen: aceptadas={len(report.accepted)} "
        f"errores={len(report.errored)} "
        f"nuevas={len(report.newly_persisted)}"
    )
    print(
        "Por tipo: "
        + " ".join(f"{k}={v}" for k, v in by_tipo.items() if v > 0)
    )
    print(
        "Por impuesto: "
        + " ".join(f"{k}={v}" for k, v in by_imp.items() if v > 0)
    )

    if args.report is not None:
        payload = {
            "today": today.isoformat(),
            "totals": {
                "accepted": len(report.accepted),
                "errored": len(report.errored),
                "newly_persisted": len(report.newly_persisted),
            },
            "by_tipo": by_tipo,
            "by_impuesto": by_imp,
            "outcomes": [
                {
                    "numero": o.numero,
                    "error": o.error,
                    "accepted": o.accepted,
                    "newly_persisted": (
                        o.persisted.was_new if o.persisted is not None else False
                    ),
                    "organo": (
                        o.resolucion.organo.value
                        if o.resolucion is not None
                        else None
                    ),
                    "tipo": (
                        o.resolucion.tipo.value
                        if o.resolucion is not None
                        else None
                    ),
                    "sentido": (
                        o.resolucion.sentido.value
                        if o.resolucion is not None
                        else None
                    ),
                    "impuesto": (
                        o.resolucion.impuesto.value
                        if o.resolucion is not None
                        else None
                    ),
                    "asunto": (
                        o.resolucion.asunto if o.resolucion is not None else None
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
