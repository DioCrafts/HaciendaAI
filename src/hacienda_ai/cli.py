"""Interfaz de línea de comandos para evaluar perfiles fiscales."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .deductions import DEFAULT_DEDUCTIONS_DIR, load_deductions
from .models import Deduction, RuleEvaluation, TaxProfile, ValidationError
from .rules import evaluate_deductions
from .simulator import SimulationReport, simulate
from .tax_calculation import TaxComparison, TaxSummary, compute_tax_comparison

STATUS_ORDER: tuple[str, ...] = (
    "applies",
    "missing_evidence",
    "missing_data",
    "pending_validation",
    "does_not_apply",
)

STATUS_LABELS: dict[str, str] = {
    "applies": "Aplica",
    "missing_evidence": "Falta documentación",
    "missing_data": "Faltan datos",
    "pending_validation": "Pendiente de validación",
    "does_not_apply": "No aplica",
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "evaluate":
        return _run_evaluate(
            profile_path=args.profile,
            deductions_path=args.deductions,
            output_format=args.format,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    if args.command == "simulate":
        return _run_simulate(
            profile_path=args.profile,
            deductions_path=args.deductions,
            output_format=args.format,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    if args.command == "serve":
        return _run_serve(
            host=args.host,
            port=args.port,
            reload=args.reload,
            api_key=args.api_key,
            stderr=sys.stderr,
        )
    if args.command == "schema":
        return _run_schema_validate(paths=args.paths, stdout=sys.stdout, stderr=sys.stderr)
    if args.command == "rag":
        return _run_rag(args, stdout=sys.stdout, stderr=sys.stderr)
    if args.command == "tax":
        return _run_tax(
            profile_path=args.profile,
            deductions_path=args.deductions,
            output_format=args.format,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    parser.error(f"Comando no soportado: {args.command}")
    return 2  # unreachable: parser.error sale con SystemExit


def entry_point() -> None:
    raise SystemExit(main())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hacienda-ai",
        description="Copiloto Fiscal IRPF España. No sustituye a un asesor fiscal.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate = subparsers.add_parser(
        "evaluate",
        help="Evalúa deducciones contra un perfil fiscal estructurado.",
    )
    evaluate.add_argument(
        "--profile",
        required=True,
        type=Path,
        help="Ruta al JSON con el perfil fiscal.",
    )
    evaluate.add_argument(
        "--deductions",
        type=Path,
        default=DEFAULT_DEDUCTIONS_DIR,
        help="Ruta al directorio o fichero JSON de deducciones.",
    )
    evaluate.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Formato de salida (por defecto: text).",
    )

    simulate_cmd = subparsers.add_parser(
        "simulate",
        help="Calcula escenarios conservador/esperado/optimizado y compara tributación individual vs conjunta.",
    )
    simulate_cmd.add_argument("--profile", required=True, type=Path)
    simulate_cmd.add_argument("--deductions", type=Path, default=DEFAULT_DEDUCTIONS_DIR)
    simulate_cmd.add_argument("--format", choices=("text", "json"), default="text")

    serve_cmd = subparsers.add_parser(
        "serve",
        help="Lanza el servidor HTTP (FastAPI + Uvicorn). Requiere instalar el extra [api].",
    )
    serve_cmd.add_argument("--host", default="127.0.0.1", help="Interfaz de escucha (por defecto: 127.0.0.1).")
    serve_cmd.add_argument("--port", type=int, default=8000, help="Puerto de escucha (por defecto: 8000).")
    serve_cmd.add_argument("--reload", action="store_true", help="Activa autoreload para desarrollo.")
    serve_cmd.add_argument(
        "--api-key",
        default=None,
        help=(
            "Si se especifica, exige el header X-API-Key en todas las llamadas /v1/* del servidor "
            "lanzado. Equivale a exportar HACIENDA_AI_API_KEY antes de arrancar."
        ),
    )

    schema_cmd = subparsers.add_parser(
        "schema",
        help="Valida ficheros JSON del corpus contra el JSON Schema empaquetado.",
    )
    schema_cmd.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="Uno o más ficheros JSON del corpus a validar.",
    )

    rag_cmd = subparsers.add_parser(
        "rag",
        help="Catálogo de fuentes oficiales: descarga, índice y búsqueda. Requiere el extra [rag].",
    )
    rag_sub = rag_cmd.add_subparsers(dest="rag_action", required=True)
    rag_list = rag_sub.add_parser("list", help="Lista el catálogo curado de fuentes oficiales.")
    rag_list.add_argument("--jurisdiction", help="Filtra por jurisdicción ('estatal' o nombre de CCAA).")
    rag_status = rag_sub.add_parser("status", help="Estado de la caché local por fuente.")
    rag_status.add_argument("--cache-dir", type=Path, default=None)
    rag_fetch = rag_sub.add_parser("fetch", help="Descarga las fuentes a la caché local.")
    rag_fetch.add_argument("--id", action="append", help="ID concreto del catálogo (repetible).")
    rag_fetch.add_argument("--all", action="store_true", help="Descarga todas las fuentes del catálogo.")
    rag_fetch.add_argument("--cache-dir", type=Path, default=None)
    rag_fetch.add_argument("--force", action="store_true", help="Redescarga aunque ya esté en caché.")
    rag_search = rag_sub.add_parser(
        "search",
        help="Busca términos en las fuentes cacheadas; devuelve snippets con la fuente original.",
    )
    rag_search.add_argument("query", help="Términos a buscar (separados por espacios).")
    rag_search.add_argument("--cache-dir", type=Path, default=None)
    rag_search.add_argument("--top", type=int, default=5)

    tax_cmd = subparsers.add_parser(
        "tax",
        help="Calcula la cuota IRPF líquida y diferencial aplicando las reglas validadas del corpus.",
    )
    tax_cmd.add_argument("--profile", required=True, type=Path)
    tax_cmd.add_argument("--deductions", type=Path, default=DEFAULT_DEDUCTIONS_DIR)
    tax_cmd.add_argument("--format", choices=("text", "json"), default="text")
    return parser


def _run_evaluate(
    *,
    profile_path: Path,
    deductions_path: Path,
    output_format: str,
    stdout: Any,
    stderr: Any,
) -> int:
    loaded = _load_profile_and_deductions(profile_path, deductions_path, stderr)
    if loaded is None:
        return 2
    profile, deductions = loaded

    evaluations = evaluate_deductions(deductions, profile)
    if output_format == "json":
        json.dump(
            [_evaluation_to_dict(evaluation) for evaluation in evaluations],
            stdout,
            ensure_ascii=False,
            indent=2,
        )
        stdout.write("\n")
    else:
        _print_text_report(evaluations, stdout)
    return 0


def _run_simulate(
    *,
    profile_path: Path,
    deductions_path: Path,
    output_format: str,
    stdout: Any,
    stderr: Any,
) -> int:
    loaded = _load_profile_and_deductions(profile_path, deductions_path, stderr)
    if loaded is None:
        return 2
    profile, deductions = loaded

    report = simulate(deductions, profile)
    if output_format == "json":
        json.dump(asdict(report), stdout, ensure_ascii=False, indent=2)
        stdout.write("\n")
    else:
        _print_simulation_report(report, stdout)
    return 0


def _load_profile_and_deductions(
    profile_path: Path, deductions_path: Path, stderr: Any
) -> tuple[TaxProfile, list[Deduction]] | None:
    try:
        profile_data = json.loads(profile_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"Error: no se encontró el archivo {profile_path}", file=stderr)
        return None
    except json.JSONDecodeError as exc:
        print(f"Error: JSON inválido en {profile_path}: {exc}", file=stderr)
        return None
    try:
        profile = TaxProfile.from_dict(profile_data)
        deductions = load_deductions(deductions_path)
    except ValidationError as exc:
        print(f"Error de validación: {exc}", file=stderr)
        return None
    except FileNotFoundError:
        print(f"Error: no se encontró el archivo {deductions_path}", file=stderr)
        return None
    return profile, deductions


def _evaluation_to_dict(evaluation: RuleEvaluation) -> dict[str, Any]:
    return asdict(evaluation)


def _run_tax(
    *,
    profile_path: Path,
    deductions_path: Path,
    output_format: str,
    stdout: Any,
    stderr: Any,
) -> int:
    loaded = _load_profile_and_deductions(profile_path, deductions_path, stderr)
    if loaded is None:
        return 2
    profile, deductions = loaded
    evaluations = evaluate_deductions(deductions, profile)
    comparison = compute_tax_comparison(profile, deductions, evaluations)
    if output_format == "json":
        json.dump(asdict(comparison), stdout, ensure_ascii=False, indent=2)
        stdout.write("\n")
    else:
        _print_tax_summary(comparison.with_rules, stdout)
        _print_tax_savings(comparison, stdout)
    return 0


def _print_tax_summary(summary: TaxSummary, stdout: Any) -> None:
    def euros(amount: float) -> str:
        return f"{amount:>12,.2f} €".replace(",", "·").replace(".", ",").replace("·", ".")

    print(
        f"Cálculo IRPF — Ejercicio {summary.tax_year}, {summary.region}",
        file=stdout,
    )
    print("", file=stdout)
    print("== Bases ==", file=stdout)
    print(f"  Base imponible general            {euros(summary.base_imponible_general)}", file=stdout)
    print(f"  Base imponible del ahorro         {euros(summary.base_imponible_ahorro)}", file=stdout)
    print(f"  - Reducciones aplicadas           {euros(summary.reducciones_aplicadas)}", file=stdout)
    print(f"  Base liquidable general           {euros(summary.base_liquidable_general)}", file=stdout)
    print(f"  Base liquidable del ahorro        {euros(summary.base_liquidable_ahorro)}", file=stdout)
    print("", file=stdout)
    print("== Mínimo personal y familiar (doble escala) ==", file=stdout)
    print(f"  Mínimo aplicado                   {euros(summary.minimum_personal_y_familiar)}", file=stdout)
    print(
        f"  Cuota correspondiente al mínimo   {euros(summary.cuota_correspondiente_al_minimo)}",
        file=stdout,
    )
    print("", file=stdout)
    print("== Cuota íntegra ==", file=stdout)
    print(f"  Tarifa general                    {euros(summary.cuota_integra_general)}", file=stdout)
    print(f"  Tarifa del ahorro                 {euros(summary.cuota_integra_ahorro)}", file=stdout)
    print(f"  Total cuota íntegra               {euros(summary.cuota_integra_total)}", file=stdout)
    print("", file=stdout)
    print("== Deducciones de cuota ==", file=stdout)
    print(f"  Deducciones (art. 68 LIRPF)       {euros(summary.deducciones_de_cuota)}", file=stdout)
    print(
        f"  Bonificaciones (Ceuta/Melilla...) {euros(summary.bonificaciones_cuota)}",
        file=stdout,
    )
    print(f"  Cuota líquida                     {euros(summary.cuota_liquida)}", file=stdout)
    print("", file=stdout)
    print("== Resultado ==", file=stdout)
    print(f"  - Retenciones e ingresos a cuenta {euros(summary.retenciones)}", file=stdout)
    diferencial_label = (
        "  Cuota diferencial (a pagar)      "
        if summary.cuota_diferencial >= 0
        else "  Cuota diferencial (a devolver)   "
    )
    print(f"{diferencial_label}{euros(summary.cuota_diferencial)}", file=stdout)
    if summary.applied_reduction_ids:
        print("", file=stdout)
        print(f"Reducciones aplicadas: {', '.join(summary.applied_reduction_ids)}", file=stdout)
    if summary.applied_cuota_deduction_ids:
        print(f"Deducciones aplicadas: {', '.join(summary.applied_cuota_deduction_ids)}", file=stdout)
    if summary.applied_bonification_ids:
        print(f"Bonificaciones aplicadas: {', '.join(summary.applied_bonification_ids)}", file=stdout)


def _print_tax_savings(comparison: TaxComparison, stdout: Any) -> None:
    def euros(amount: float) -> str:
        return f"{amount:>12,.2f} €".replace(",", "·").replace(".", ",").replace("·", ".")

    print("", file=stdout)
    print("== Ahorro fiscal real (con reglas vs. sin reglas) ==", file=stdout)
    print(
        f"  Cuota diferencial sin reglas      {euros(comparison.without_rules.cuota_diferencial)}",
        file=stdout,
    )
    print(
        f"  Cuota diferencial con reglas      {euros(comparison.with_rules.cuota_diferencial)}",
        file=stdout,
    )
    print(f"  Ahorro real                       {euros(comparison.ahorro_real)}", file=stdout)
    contributing = [saving for saving in comparison.savings_per_rule if saving.ahorro_marginal > 0]
    if contributing:
        print("", file=stdout)
        print("  Ahorro marginal por regla (no suma exactamente el agregado):", file=stdout)
        for saving in sorted(contributing, key=lambda item: -item.ahorro_marginal):
            print(
                f"    {saving.deduction_id:<55s}{euros(saving.ahorro_marginal)}",
                file=stdout,
            )


def _run_serve(*, host: str, port: int, reload: bool, api_key: str | None, stderr: Any) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "Error: el subcomando 'serve' requiere el extra [api]. "
            "Instala las dependencias HTTP con: pip install 'hacienda-ai[api]'",
            file=stderr,
        )
        return 2
    if api_key is not None:
        import os

        os.environ["HACIENDA_AI_API_KEY"] = api_key
    uvicorn.run("hacienda_ai.api:app", host=host, port=port, reload=reload)
    return 0


def _run_rag(args: argparse.Namespace, *, stdout: Any, stderr: Any) -> int:
    try:
        from .rag.ingestion.fetcher import (
            DEFAULT_CACHE_DIR,
            cache_status,
            fetch_all,
            fetch_source,
        )
        from .rag.retrieval.search import search
        from .rag.sources.catalog import CATALOG
    except ImportError as exc:
        print(
            f"Error: el subcomando 'rag' requiere el extra [rag] ({exc.name}). "
            "Instala las dependencias con: pip install 'hacienda-ai[rag]'",
            file=stderr,
        )
        return 2

    cache_dir: Path = getattr(args, "cache_dir", None) or DEFAULT_CACHE_DIR

    if args.rag_action == "list":
        jurisdiction = (args.jurisdiction or "").strip().lower()
        for source in CATALOG:
            if jurisdiction and source.jurisdiction.lower() != jurisdiction:
                continue
            print(f"{source.id}\t{source.jurisdiction}\t{source.document_type}\t{source.url}", file=stdout)
        return 0

    if args.rag_action == "status":
        for entry in cache_status(cache_dir):
            if entry.get("cached"):
                print(
                    f"{entry['id']}\tcached\t{entry.get('size_bytes')} bytes\t{entry.get('fetched_at')}",
                    file=stdout,
                )
            else:
                print(f"{entry['id']}\tmissing", file=stdout)
        return 0

    if args.rag_action == "fetch":
        targets: tuple[Any, ...] = ()
        if args.all:
            targets = CATALOG
        elif args.id:
            ids = set(args.id)
            targets = tuple(source for source in CATALOG if source.id in ids)
            missing = ids - {source.id for source in CATALOG}
            if missing:
                print(
                    f"Error: id desconocido en el catálogo: {', '.join(sorted(missing))}",
                    file=stderr,
                )
                return 2
        else:
            print(
                "Error: indica --all o --id ID (repetible) para descargar fuentes.",
                file=stderr,
            )
            return 2
        try:
            if args.all:
                results = fetch_all(cache_dir, force=args.force, sources=targets)
            else:
                results = [fetch_source(source, cache_dir, force=args.force) for source in targets]
        except Exception as exc:
            print(f"Error durante la descarga: {exc}", file=stderr)
            return 1
        for result in results:
            label = "skipped" if result.skipped else f"{result.size_bytes} bytes"
            print(f"{result.source_id}\t{label}\t{result.path}", file=stdout)
        return 0

    if args.rag_action == "search":
        hits = search(args.query, cache_dir=cache_dir, top_k=args.top)
        if not hits:
            print(
                "Sin coincidencias. Descarga primero las fuentes con 'hacienda-ai rag fetch --all'.",
                file=stdout,
            )
            return 0
        for hit in hits:
            print(f"{hit.source.id}\tscore={hit.score}\t{hit.source.url}", file=stdout)
            if hit.snippet:
                print(f"  {hit.snippet}", file=stdout)
        return 0

    print(f"Acción RAG no soportada: {args.rag_action}", file=stderr)
    return 2


def _run_schema_validate(*, paths: list[Path], stdout: Any, stderr: Any) -> int:
    try:
        import jsonschema
    except ImportError:
        print(
            "Error: el subcomando 'schema' requiere el extra [dev]. "
            "Instala las dependencias con: pip install 'hacienda-ai[dev]'",
            file=stderr,
        )
        return 2
    schema_path = Path(__file__).parent / "data" / "corpus.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors_found = False
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            print(f"{path}: no se encontró el archivo", file=stderr)
            errors_found = True
            continue
        except json.JSONDecodeError as exc:
            print(f"{path}: JSON inválido: {exc}", file=stderr)
            errors_found = True
            continue
        errors = list(validator.iter_errors(data))
        if errors:
            errors_found = True
            for err in errors:
                location = "/".join(str(part) for part in err.absolute_path) or "/"
                print(f"{path}: {location}: {err.message}", file=stderr)
        else:
            print(f"{path}: OK", file=stdout)
    return 1 if errors_found else 0


def _print_simulation_report(report: SimulationReport, stdout: Any) -> None:
    print(
        f"Simulación fiscal — Ejercicio {report.tax_year}, {report.region} "
        f"(modo declarado: {report.requested_filing_mode})",
        file=stdout,
    )
    print(f"Modo recomendado por importe estimado: {report.recommended_filing_mode}", file=stdout)
    print("", file=stdout)
    for filing in (report.individual, report.conjunta):
        label = "Tributación individual" if filing.filing_mode == "individual" else "Tributación conjunta"
        print(f"== {label} ==", file=stdout)
        for scenario in filing.scenarios:
            count = len(scenario.included_deduction_ids)
            noun = "deducción" if count == 1 else "deducciones"
            print(
                f"- {scenario.name}: {scenario.total_estimated_amount:.2f} € ({count} {noun})",
                file=stdout,
            )
            if scenario.included_deduction_ids:
                print(f"    Incluye: {', '.join(scenario.included_deduction_ids)}", file=stdout)
        print("", file=stdout)


def _print_text_report(evaluations: list[RuleEvaluation], stdout: Any) -> None:
    grouped: dict[str, list[RuleEvaluation]] = {}
    for evaluation in evaluations:
        grouped.setdefault(evaluation.status, []).append(evaluation)

    total = sum(e.estimated_amount for e in evaluations if e.status in {"applies", "missing_evidence"})
    print(f"Deducciones evaluadas: {len(evaluations)}", file=stdout)
    print(f"Importe estimado (applies + missing_evidence): {total:.2f} €", file=stdout)
    print("", file=stdout)

    for status in STATUS_ORDER:
        items = grouped.get(status, [])
        if not items:
            continue
        label = STATUS_LABELS.get(status, status)
        print(f"== {label} ({len(items)}) ==", file=stdout)
        for evaluation in items:
            print(
                f"- {evaluation.deduction_id}: {evaluation.estimated_amount:.2f} € — {evaluation.reason}",
                file=stdout,
            )
            if evaluation.missing_fields:
                print(f"    Campos faltantes: {', '.join(evaluation.missing_fields)}", file=stdout)
            if evaluation.missing_documents:
                print(f"    Documentos faltantes: {', '.join(evaluation.missing_documents)}", file=stdout)
        print("", file=stdout)
