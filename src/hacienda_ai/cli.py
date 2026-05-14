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
