"""Comprobador de freshness del corpus auditable.

El corpus de HaciendaAI envejece silenciosamente si la cadena de
ingestión falla: el cron de BOE puede romper sin que nadie se entere,
y al cabo de unas semanas las respuestas del asesor citan normativa
desactualizada con verdict `safe` (porque el guard verifica vigencia
contra el último estado conocido, no contra "¿este estado es reciente?").

Este script da una segunda red de seguridad orthogonal al
`citation_guard`: si la "última norma BOE-A registrada" lleva semanas
sin moverse, o si las deducciones llevan meses sin revisarse, levanta
la alarma con código de salida 1 para que el workflow GitHub abra
issue.

Checks (todos opt-in, configurables vía CLI):

1. **Edad de la norma BOE-A más reciente** (`--max-boe-age-days`,
   default 30): si la `enacted_at` más reciente del registry de normas
   es mayor que ese umbral, marca stale. 30 días es generoso para
   "el BOE no publicó nada relevante" pero un mes seguido sin
   novedades fiscales es muy improbable.

2. **Edad de la última revisión de deducciones** (`--max-deduction-review-age-days`,
   default 180): si ninguna deducción tiene `last_reviewed_at` reciente,
   la curaduría del corpus está parada. 180 días = 6 meses cubre el
   ciclo anual de campaña Renta.

3. **Edad del último item de jurisprudencia/DGT/TEAC ingerido**
   (`--max-jurisprudence-age-days`, default 90): si existen los
   subdirectorios y todos los `last_fetched_at` son antiguos, los
   operadores no están manteniendo el corpus doctrinal. Si los
   directorios no existen, el check se OMITE (no es obligatorio
   tener corpus de jurisprudencia para que el sistema funcione).

Salidas:
0 — corpus fresco según TODOS los criterios aplicables.
1 — al menos un criterio falla. Detalles en stdout (humano) y en
    `--report` (JSON estructurado, consumible por el workflow para
    generar el body del issue).
2 — error fatal (registro corrupto, --max-* negativo, etc.).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from hacienda_ai.deductions import load_deductions  # noqa: E402
from hacienda_ai.models import (  # noqa: E402
    ConsultaDGT,
    Deduction,
    NormaRegistry,
    ResolucionTEAC,
    Sentencia,
)
from hacienda_ai.normas import load_norma_registry  # noqa: E402

DEFAULT_DATA_DIR = REPO_ROOT / "src" / "hacienda_ai" / "data"


@dataclass
class CheckResult:
    """Resultado de un check individual."""

    name: str
    description: str
    is_fresh: bool
    max_age_days: int
    latest_age_days: int | None
    latest_item_id: str | None
    latest_item_date: str | None
    skipped: bool = False
    skip_reason: str | None = None


@dataclass
class FreshnessReport:
    """Reporte agregado de freshness."""

    today: str
    is_fresh: bool
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def stale_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.is_fresh and not c.skipped]

    def to_dict(self) -> dict[str, Any]:
        return {
            "today": self.today,
            "is_fresh": self.is_fresh,
            "checks": [asdict(c) for c in self.checks],
        }


def check_norma_freshness(
    registry: NormaRegistry, today: date, max_age_days: int
) -> CheckResult:
    """Comprueba que la norma más reciente del registry es razonablemente
    reciente. Usa `enacted_at` (no `effective_from`) porque queremos
    detectar si el cron de ingesta está vivo, no si la norma está
    vigente: una norma promulgada hoy con `effective_from` en 2027
    cuenta como señal de que el cron está funcionando."""
    latest_date: date | None = None
    latest_id: str | None = None
    for boe_id in registry.all_boe_ids():
        norma = registry.get_norma(boe_id)
        if norma is None:
            continue
        if latest_date is None or norma.enacted_at > latest_date:
            latest_date = norma.enacted_at
            latest_id = boe_id

    if latest_date is None:
        return CheckResult(
            name="norma_registry",
            description="Edad de la norma BOE-A más reciente del registry",
            is_fresh=False,
            max_age_days=max_age_days,
            latest_age_days=None,
            latest_item_id=None,
            latest_item_date=None,
        )
    age = (today - latest_date).days
    return CheckResult(
        name="norma_registry",
        description="Edad de la norma BOE-A más reciente del registry",
        is_fresh=age <= max_age_days,
        max_age_days=max_age_days,
        latest_age_days=age,
        latest_item_id=latest_id,
        latest_item_date=latest_date.isoformat(),
    )


def check_deductions_freshness(
    deductions: list[Deduction], today: date, max_age_days: int
) -> CheckResult:
    """Comprueba que al menos una deducción ha sido revisada recientemente.

    Si NINGUNA deducción tiene `last_reviewed_at` (todas son None), el
    check se considera fallido — la curaduría del corpus está rota o
    nadie marca las revisiones."""
    latest_date: date | None = None
    latest_id: str | None = None
    for d in deductions:
        if d.last_reviewed_at is None:
            continue
        if latest_date is None or d.last_reviewed_at > latest_date:
            latest_date = d.last_reviewed_at
            latest_id = d.id

    if latest_date is None:
        return CheckResult(
            name="deductions_review",
            description="Edad de la última revisión humana de deducciones",
            is_fresh=False,
            max_age_days=max_age_days,
            latest_age_days=None,
            latest_item_id=None,
            latest_item_date=None,
        )
    age = (today - latest_date).days
    return CheckResult(
        name="deductions_review",
        description="Edad de la última revisión humana de deducciones",
        is_fresh=age <= max_age_days,
        max_age_days=max_age_days,
        latest_age_days=age,
        latest_item_id=latest_id,
        latest_item_date=latest_date.isoformat(),
    )


def check_jurisprudence_freshness(
    data_dir: Path, today: date, max_age_days: int
) -> CheckResult:
    """Comprueba la antigüedad del item de jurisprudencia/DGT/TEAC más
    reciente. Si ninguno de los tres subdirectorios existe, el check se
    OMITE (corpus doctrinal no es obligatorio)."""
    subdirs = {
        "jurisprudencia": Sentencia,
        "dgt_consultas": ConsultaDGT,
        "teac_resoluciones": ResolucionTEAC,
    }
    existing = {name: cls for name, cls in subdirs.items() if (data_dir / name).exists()}
    if not existing:
        return CheckResult(
            name="jurisprudence_corpus",
            description=(
                "Edad del item jurisprudencia/DGT/TEAC más reciente"
            ),
            is_fresh=True,
            max_age_days=max_age_days,
            latest_age_days=None,
            latest_item_id=None,
            latest_item_date=None,
            skipped=True,
            skip_reason=(
                "Ningún subdirectorio de jurisprudencia/dgt/teac existe; "
                "el corpus doctrinal aún no está sembrado."
            ),
        )

    latest_date: date | None = None
    latest_id: str | None = None
    for subdir_name, cls in existing.items():
        for path in sorted((data_dir / subdir_name).rglob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                item = cls.from_dict(data)
            except Exception:
                # Item corrupto: lo saltamos. verify-seed lo detectará.
                continue
            fetched = item.last_fetched_at
            if latest_date is None or fetched > latest_date:
                latest_date = fetched
                latest_id = _item_id(item)

    if latest_date is None:
        return CheckResult(
            name="jurisprudence_corpus",
            description="Edad del item jurisprudencia/DGT/TEAC más reciente",
            is_fresh=False,
            max_age_days=max_age_days,
            latest_age_days=None,
            latest_item_id=None,
            latest_item_date=None,
        )
    age = (today - latest_date).days
    return CheckResult(
        name="jurisprudence_corpus",
        description="Edad del item jurisprudencia/DGT/TEAC más reciente",
        is_fresh=age <= max_age_days,
        max_age_days=max_age_days,
        latest_age_days=age,
        latest_item_id=latest_id,
        latest_item_date=latest_date.isoformat(),
    )


def _item_id(item: Sentencia | ConsultaDGT | ResolucionTEAC) -> str:
    if isinstance(item, Sentencia):
        return item.ecli
    return item.numero


def build_report(
    *,
    today: date,
    registry: NormaRegistry,
    deductions: list[Deduction],
    data_dir: Path,
    max_boe_age_days: int,
    max_deduction_review_age_days: int,
    max_jurisprudence_age_days: int,
) -> FreshnessReport:
    """Construye el reporte completo ejecutando los tres checks."""
    checks = [
        check_norma_freshness(registry, today, max_boe_age_days),
        check_deductions_freshness(
            deductions, today, max_deduction_review_age_days
        ),
        check_jurisprudence_freshness(
            data_dir, today, max_jurisprudence_age_days
        ),
    ]
    is_fresh = all(c.is_fresh for c in checks)
    return FreshnessReport(
        today=today.isoformat(),
        is_fresh=is_fresh,
        checks=checks,
    )


def render_summary(report: FreshnessReport) -> str:
    """Resumen humano para stdout/PR comment."""
    lines = [
        f"Corpus freshness report — {report.today}",
        f"Veredicto: {'FRESH' if report.is_fresh else 'STALE'}",
        "",
    ]
    for c in report.checks:
        icon = "·" if c.skipped else ("+" if c.is_fresh else "✗")
        if c.skipped:
            lines.append(f"  {icon} [{c.name}] SKIPPED: {c.skip_reason}")
            continue
        if c.latest_age_days is None:
            lines.append(
                f"  {icon} [{c.name}] FAIL: no se encontró ningún item. "
                f"({c.description})"
            )
            continue
        verdict = "OK" if c.is_fresh else "STALE"
        lines.append(
            f"  {icon} [{c.name}] {verdict}: edad {c.latest_age_days}d "
            f"(umbral {c.max_age_days}d). Último: {c.latest_item_id} "
            f"({c.latest_item_date}). {c.description}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verifica que el corpus auditable se ha refrescado dentro de "
            "los umbrales configurados. Pensado como cron diario que abre "
            "issue cuando la cadena de ingestión falla en silencio."
        )
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Raíz del corpus (default: src/hacienda_ai/data/).",
    )
    parser.add_argument(
        "--max-boe-age-days",
        type=int,
        default=30,
        help=(
            "Edad máxima (días) de la norma BOE-A más reciente. Default 30. "
            "Subir si el repo tiene corpus histórico congelado a propósito."
        ),
    )
    parser.add_argument(
        "--max-deduction-review-age-days",
        type=int,
        default=180,
        help=(
            "Edad máxima (días) de la última deducción revisada por humano. "
            "Default 180 (6 meses, cubre ciclo anual Renta)."
        ),
    )
    parser.add_argument(
        "--max-jurisprudence-age-days",
        type=int,
        default=90,
        help=(
            "Edad máxima (días) del item más reciente de "
            "jurisprudencia/DGT/TEAC. Default 90. Si los subdirectorios no "
            "existen, el check se omite."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Si se indica, escribe un JSON estructurado en esta ruta.",
    )
    parser.add_argument(
        "--today",
        type=str,
        default=None,
        help=(
            "Fecha de referencia (YYYY-MM-DD) para los checks. Default: "
            "fecha actual UTC. Útil para tests y backfills deterministas."
        ),
    )
    args = parser.parse_args(argv)

    for name, value in (
        ("--max-boe-age-days", args.max_boe_age_days),
        ("--max-deduction-review-age-days", args.max_deduction_review_age_days),
        ("--max-jurisprudence-age-days", args.max_jurisprudence_age_days),
    ):
        if value < 0:
            print(f"ERROR: {name} debe ser >= 0 (recibido {value})", file=sys.stderr)
            return 2

    if args.today is not None:
        try:
            today = date.fromisoformat(args.today)
        except ValueError as exc:
            print(
                f"ERROR: --today debe ser ISO 8601 (YYYY-MM-DD): {exc}",
                file=sys.stderr,
            )
            return 2
    else:
        # Hoy UTC para que el cron sea reproducible independientemente
        # del runner (los GitHub Actions runners están en UTC pero
        # mejor explícito).
        today = date.today()

    try:
        registry = load_norma_registry()
        deductions = load_deductions()
    except Exception as exc:  # noqa: BLE001 — visibilidad operativa
        print(f"ERROR fatal cargando corpus: {exc}", file=sys.stderr)
        return 2

    report = build_report(
        today=today,
        registry=registry,
        deductions=deductions,
        data_dir=args.data_dir,
        max_boe_age_days=args.max_boe_age_days,
        max_deduction_review_age_days=args.max_deduction_review_age_days,
        max_jurisprudence_age_days=args.max_jurisprudence_age_days,
    )

    print(render_summary(report))

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\nReporte JSON escrito en {args.report}")

    return 0 if report.is_fresh else 1


if __name__ == "__main__":
    sys.exit(main())
