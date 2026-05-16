"""Análisis de impacto: cruce de drift detectado con el corpus auditable.

El verificador `scripts/verify_seed.py` detecta cambios entre el corpus
local y el texto consolidado del BOE; este módulo responde a la pregunta
operativa: *si cambia el art. X de la norma Y, ¿qué deducciones y escalas
de mi corpus se ven afectadas?*

El reporte generado se renderiza como markdown listo para el body de un
issue de GitHub. La GitHub Action diaria lee el JSON producido por el
script y abre/actualiza el issue con esta sección de impacto.

Diseño:
- Tipos `DriftItem` y `BrokenRegionalURL` son JSON-friendly (dataclasses
  congeladas con `.to_dict()`).
- `analyze_impact()` toma esos items y un corpus + escalas, y produce un
  `ImpactReport` con el cruce.
- `render_markdown(report)` no consulta red, solo formatea — testeable
  contra un golden si hace falta.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..irpf.scales import TaxScale
from ..models import Deduction


@dataclass(frozen=True)
class DriftItem:
    """Una divergencia detectada entre el corpus local y BOE.

    `boe_id` + `article` localizan la cita. `declared_hash` es lo que el
    corpus tenía cuando se subió; `computed_hash` es lo que ahora dice el
    BOE. `deduction_id` identifica la primera regla del corpus que disparó
    la verificación (informativo: las demás reglas afectadas se calculan
    luego en `analyze_impact`).
    """

    boe_id: str
    article: str
    declared_hash: str | None
    computed_hash: str
    deduction_id: str

    @property
    def key(self) -> str:
        return f"{self.boe_id}|{self.article}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "boe_id": self.boe_id,
            "article": self.article,
            "declared_hash": self.declared_hash,
            "computed_hash": self.computed_hash,
            "deduction_id": self.deduction_id,
        }


@dataclass(frozen=True)
class BrokenRegionalURL:
    """Una URL de fuente autonómica que devuelve 404/5xx o no responde.

    Los boletines regionales no tienen API consolidada con verificador
    SHA-256 (esa rama del corpus se acepta sin hash), pero sí podemos
    detectar enlaces rotos de las URLs declaradas en las deducciones:
    una redirección desde el boletín al portal genérico, un 404, o un
    timeout son señales de que la cita necesita revisión manual.
    """

    url: str
    boe_id: str | None
    deduction_id: str
    status_code: int | None
    error: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "boe_id": self.boe_id,
            "deduction_id": self.deduction_id,
            "status_code": self.status_code,
            "error": self.error,
        }


@dataclass(frozen=True)
class ImpactReport:
    """Cruce entre drift y corpus.

    `affected_deductions`/`affected_scales` están indexados por la `key`
    de cada `DriftItem` (`boe_id|article`), y devuelven la lista ordenada
    de ids del corpus que citan esa fuente.
    """

    drift_items: tuple[DriftItem, ...]
    broken_urls: tuple[BrokenRegionalURL, ...]
    affected_deductions: dict[str, list[str]] = field(default_factory=dict)
    affected_scales: dict[str, list[str]] = field(default_factory=dict)

    @property
    def has_findings(self) -> bool:
        return bool(self.drift_items) or bool(self.broken_urls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "drift_items": [d.to_dict() for d in self.drift_items],
            "broken_urls": [u.to_dict() for u in self.broken_urls],
            "affected_deductions": self.affected_deductions,
            "affected_scales": self.affected_scales,
        }


def _normalize_article(article: str | None) -> str | None:
    if article is None:
        return None
    return article.strip().lower()


def _index_corpus(
    corpus: list[Deduction],
    scales: list[TaxScale],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Construye índices `(boe_id|article) -> [id, ...]` para deducciones y escalas.

    Las claves se forman normalizando minúsculas y espacios para que
    `art. 57` y `Art. 57 ` colapsen al mismo bucket; el contenido de la
    lista mantiene los ids canónicos del corpus.
    """
    ded_index: dict[str, list[str]] = {}
    for d in corpus:
        for src in d.sources:
            if src.boe_id is None or src.article is None:
                continue
            key = f"{src.boe_id}|{_normalize_article(src.article)}"
            ded_index.setdefault(key, []).append(d.id)

    scale_index: dict[str, list[str]] = {}
    for s in scales:
        for src in s.sources:
            if src.boe_id is None or src.article is None:
                continue
            key = f"{src.boe_id}|{_normalize_article(src.article)}"
            scale_index.setdefault(key, []).append(s.id)

    for index in (ded_index, scale_index):
        for key in list(index):
            index[key] = sorted(set(index[key]))
    return ded_index, scale_index


def analyze_impact(
    drift_items: list[DriftItem],
    broken_urls: list[BrokenRegionalURL],
    corpus: list[Deduction],
    scales: list[TaxScale],
) -> ImpactReport:
    """Cruza el drift detectado con el corpus para listar afectados."""
    ded_index, scale_index = _index_corpus(corpus, scales)
    affected_ded: dict[str, list[str]] = {}
    affected_scales: dict[str, list[str]] = {}
    for item in drift_items:
        key = f"{item.boe_id}|{_normalize_article(item.article)}"
        if key in ded_index:
            affected_ded[item.key] = ded_index[key]
        if key in scale_index:
            affected_scales[item.key] = scale_index[key]
    return ImpactReport(
        drift_items=tuple(drift_items),
        broken_urls=tuple(broken_urls),
        affected_deductions=affected_ded,
        affected_scales=affected_scales,
    )


def render_markdown(report: ImpactReport) -> str:
    """Markdown listo para el body de un issue de GitHub.

    Estructura: cabecera + tabla por cada `DriftItem` con su lista de
    deducciones/escalas afectadas + sección final con URLs rotas. Si el
    reporte no tiene findings, devuelve una línea estable que la Action
    detecta para no abrir issue.
    """
    if not report.has_findings:
        return "_Sin findings: el corpus está alineado con BOE y los enlaces autonómicos responden correctamente._"

    lines: list[str] = []
    if report.drift_items:
        lines.append("## Drift detectado contra BOE consolidado\n")
        for item in report.drift_items:
            affected_ded = report.affected_deductions.get(item.key, [])
            affected_scl = report.affected_scales.get(item.key, [])
            lines.append(f"### {item.boe_id} {item.article}\n")
            declared = item.declared_hash or "_ausente_"
            lines.append(f"- Hash declarado: `{declared}`")
            lines.append(f"- Hash calculado: `{item.computed_hash}`")
            lines.append(f"- Primera cita detectada: `{item.deduction_id}`")
            if affected_ded:
                lines.append("- **Deducciones que citan esta fuente:**")
                for did in affected_ded:
                    lines.append(f"  - `{did}`")
            if affected_scl:
                lines.append("- **Escalas que citan esta fuente:**")
                for sid in affected_scl:
                    lines.append(f"  - `{sid}`")
            if not affected_ded and not affected_scl:
                lines.append("- _Ninguna deducción/escala del corpus actual cita esta fuente._")
            lines.append("")

    if report.broken_urls:
        lines.append("## Enlaces a boletines autonómicos rotos\n")
        lines.append("| Boletín | URL | Estado | Deducción |")
        lines.append("|---|---|---|---|")
        for u in report.broken_urls:
            status = (
                f"HTTP {u.status_code}"
                if u.status_code is not None
                else (u.error or "sin respuesta")
            )
            boe_label = u.boe_id or "—"
            lines.append(f"| {boe_label} | {u.url} | {status} | `{u.deduction_id}` |")
        lines.append("")
        lines.append(
            "Los boletines autonómicos no tienen verificador SHA-256: solo "
            "se detectan URLs rotas (404, 5xx, timeout). Revisar manualmente "
            "si el documento sigue siendo accesible bajo otra URL canónica."
        )

    return "\n".join(lines).rstrip() + "\n"


def write_report_json(report: ImpactReport, path: Path) -> None:
    path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
