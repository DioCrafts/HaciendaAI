"""Ingesta de manuales AEAT (PDF) y FAQs INFORMA (HTML).

Modos:

    # Manual Práctico IRPF 2024 (PDF real, requiere pypdf instalado)
    python scripts/ingest_manual_aeat.py \\
        --pdf /path/Manual_Practico_IRPF_2024.pdf \\
        --fuente manual_irpf --ejercicio 2024

    # Manual Práctico IS 2023 (PDF)
    python scripts/ingest_manual_aeat.py \\
        --pdf /path/Manual_IS_2023.pdf \\
        --fuente manual_is --ejercicio 2023

    # Texto plano (separado por \\f) — útil cuando ya tienes el manual
    # convertido por OCR o por otra herramienta.
    python scripts/ingest_manual_aeat.py \\
        --pdf /path/manual_irpf_2024.txt --txt \\
        --fuente manual_irpf --ejercicio 2024

    # FAQs INFORMA (HTML descargado del buscador)
    python scripts/ingest_manual_aeat.py --informa /path/faqs.html

Códigos de salida:
    0 — ejecución correcta (chunks generados o sin contenido).
    1 — error al extraer/parsear el material.
    2 — error fatal (args inválidos, dependencia ausente).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from hacienda_ai.models import ManualFuente  # noqa: E402
from hacienda_ai.rag.manuals import (  # noqa: E402
    ChunkingConfig,
    StubPdfExtractor,
    ingest_informa_html,
    ingest_manual_pdf,
)

DEFAULT_ROOT_DIR = REPO_ROOT / "src" / "hacienda_ai" / "data" / "manuales"


def _parse_fuente(raw: str) -> ManualFuente:
    try:
        return ManualFuente(raw)
    except ValueError as exc:
        allowed = ", ".join(f.value for f in ManualFuente)
        raise argparse.ArgumentTypeError(
            f"fuente inválida {raw!r}; admitidos: {allowed}"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingesta de manuales AEAT (PDF) y FAQs INFORMA (HTML)."
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        default=None,
        help="Ruta al PDF (o .txt si se usa --txt) del Manual Práctico.",
    )
    parser.add_argument(
        "--txt",
        action="store_true",
        help="El input --pdf es texto plano (páginas separadas por \\f).",
    )
    parser.add_argument(
        "--informa",
        type=Path,
        default=None,
        help="Ruta al HTML del buscador INFORMA.",
    )
    parser.add_argument(
        "--fuente",
        type=_parse_fuente,
        default=None,
        help="Fuente del manual (manual_irpf, manual_is, manual_iva...).",
    )
    parser.add_argument("--ejercicio", type=int, default=None)
    parser.add_argument(
        "--url",
        default=None,
        help="URL canónica del manual (opcional, se persiste en cada chunk).",
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT_DIR)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--min-words", type=int, default=50)
    parser.add_argument("--target-words", type=int, default=400)
    parser.add_argument("--max-words", type=int, default=800)
    args = parser.parse_args(argv)

    if args.pdf is None and args.informa is None:
        print("ERROR: usa --pdf o --informa.", file=sys.stderr)
        return 2
    if args.pdf is not None and args.informa is not None:
        print("ERROR: --pdf y --informa son mutuamente excluyentes.", file=sys.stderr)
        return 2

    today = date.today()

    if args.informa is not None:
        report = ingest_informa_html(
            args.informa,
            today=today,
            root_dir=args.root,
            url_fuente=args.url,
            persist=not args.dry_run,
        )
    else:
        if args.fuente is None:
            print(
                "ERROR: --pdf requiere --fuente (manual_irpf|manual_is|...).",
                file=sys.stderr,
            )
            return 2
        config = ChunkingConfig(
            min_words=args.min_words,
            target_words=args.target_words,
            max_words=args.max_words,
        )
        extractor = StubPdfExtractor() if args.txt else None
        report = ingest_manual_pdf(
            args.pdf,
            fuente=args.fuente,
            ejercicio=args.ejercicio,
            today=today,
            root_dir=args.root,
            extractor=extractor,
            config=config,
            url_fuente=args.url,
            persist=not args.dry_run,
        )

    if report.error:
        print(f"ERROR: {report.error}", file=sys.stderr)
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(
                    {"error": report.error, "today": today.isoformat()},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        return 1

    print(f"Chunks generados: {len(report.chunks)}")
    if report.persisted:
        new_count = len(report.newly_persisted)
        print(
            f"Persistidos: {len(report.persisted)} "
            f"(nuevos: {new_count}, duplicados: {len(report.persisted) - new_count})"
        )

    if args.report is not None:
        payload = {
            "today": today.isoformat(),
            "total_chunks": len(report.chunks),
            "newly_persisted": len(report.newly_persisted),
            "chunks": [
                {
                    "chunk_id": c.chunk_id,
                    "fuente": c.fuente.value,
                    "titulo": c.titulo,
                    "page_inicio": c.page_inicio,
                    "referencias": list(c.referencias_normativas),
                }
                for c in report.chunks
            ],
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"Reporte JSON escrito en {args.report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
