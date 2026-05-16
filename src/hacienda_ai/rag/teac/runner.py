"""Orquestador del pipeline de ingesta TEAC/TEAR.

Para cada número de reclamación solicitado:

1. Resuelve a HTML via `TeacClient` (local o HTTP).
2. Parsea con `parse_resolucion_html` → estructura intermedia.
3. Determina órgano (TEAC/TEAR/TEAL) por código de TEA + contenido.
4. Detecta tipo de resolución (unifica criterio / extiende efectos /
   ordinaria), sentido, impuesto principal.
5. Extrae criterio doctrinal y normativa citada.
6. Calcula hash y ensambla `ResolucionTEAC`.
7. Persiste a `data/teac_resoluciones/<organo>/<año>/<num_safe>.json`.

Sin filtro fiscal (vía económico-administrativa = siempre tributario).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ...models import (
    CriterioConfidence,
    Impuesto,
    OrganoTEA,
    ResolucionTEAC,
    TipoResolucion,
)
from .client import TeacClient, TeacFetchError
from .extractors import (
    detect_impuesto,
    detect_sentido,
    detect_tipo,
    extract_criterio,
    extract_normativa,
)
from .numero import (
    NumeroReclamacion,
    NumeroReclamacionParseError,
    parse_numero_reclamacion,
)
from .parser import (
    ParsedResolucion,
    ResolucionParseError,
    parse_resolucion_date,
    parse_resolucion_html,
)
from .persistence import PersistedResolucion, persist_resolucion


@dataclass(frozen=True)
class ResolucionOutcome:
    """Resultado del procesamiento de UN número."""

    numero: str
    resolucion: ResolucionTEAC | None
    persisted: PersistedResolucion | None
    error: str | None

    @property
    def accepted(self) -> bool:
        return self.resolucion is not None


@dataclass
class IngestionReport:
    today: date
    outcomes: list[ResolucionOutcome] = field(default_factory=list)

    @property
    def accepted(self) -> list[ResolucionOutcome]:
        return [o for o in self.outcomes if o.accepted]

    @property
    def errored(self) -> list[ResolucionOutcome]:
        return [o for o in self.outcomes if o.error is not None]

    @property
    def newly_persisted(self) -> list[ResolucionOutcome]:
        return [
            o for o in self.outcomes
            if o.persisted is not None and o.persisted.was_new
        ]


def _determine_organo(
    numero: NumeroReclamacion, parsed: ParsedResolucion
) -> tuple[OrganoTEA, str | None]:
    """Decide órgano (TEAC/TEAR/TEAL) y sede.

    Reglas:
    - Si el campo `Órgano` o `Unidad Resolutoria` de la cabecera menciona
      "Central" → TEAC.
    - Si menciona "Local" o sale en la lista de TEALs (Madrid, Barcelona) → TEAL.
    - Si código TEA = 0 → TEAC (fallback).
    - Si código TEA = 1-52 → TEAR/TEAL: distinguir por contenido.
    """
    organo_text = (
        parsed.get_field("Órgano")
        or parsed.get_field("Unidad Resolutoria")
        or parsed.get_field("Sala")
        or ""
    )
    sede = parsed.get_field("Sede") or organo_text or None
    norm_lower = organo_text.lower()

    if "central" in norm_lower:
        return OrganoTEA.TEAC, sede
    if "local" in norm_lower:
        return OrganoTEA.TEAL, sede
    if "regional" in norm_lower:
        return OrganoTEA.TEAR, sede

    # Fallback por código TEA del número.
    if numero.is_teac_central:
        return OrganoTEA.TEAC, sede
    return OrganoTEA.TEAR, sede


def _first_meaningful_line(text: str) -> str:
    """Primera línea no trivial de un texto. Útil para extraer asunto si falta."""
    for line in text.split("\n"):
        line = line.strip()
        if line and len(line) > 10 and not line.lower().startswith(
            ("antecedentes", "fundamentos", "criterio", "fallo")
        ):
            return line[:200]
    return ""


def _build_resolucion(
    numero: NumeroReclamacion,
    parsed: ParsedResolucion,
    *,
    today: date,
    source_url: str | None,
) -> ResolucionTEAC:
    """Convierte `ParsedResolucion` en `ResolucionTEAC` del modelo de dominio."""
    fecha_raw = parsed.get_field("Fecha")
    fecha = parse_resolucion_date(fecha_raw) if fecha_raw else None
    if fecha is None:
        raise ResolucionParseError(
            f"{numero.canonical}: cabecera sin campo Fecha parseable"
        )

    organo, sede = _determine_organo(numero, parsed)

    materia = (
        parsed.get_field("Materia", "Concepto")
        or parsed.get_field("Asunto")
        or ""
    )
    asunto = (
        materia
        or _first_meaningful_line(parsed.secciones.get("ANTECEDENTES", ""))
        or "(sin asunto)"
    )

    tipo = detect_tipo(
        tipo_header=parsed.get_field("Tipo de Resolución"),
        asunto=asunto,
        cuerpo=parsed.plain_text,
    )

    sentido = detect_sentido(
        parsed.plain_text, parsed.secciones.get("FALLO")
    )

    impuesto = detect_impuesto(
        normativa=parsed.get_field("Normativa"),
        materia=materia,
        cuerpo=parsed.plain_text,
    )

    criterio = extract_criterio(
        parsed.plain_text,
        criterio_section=parsed.secciones.get("CRITERIO"),
        fundamentos_section=parsed.secciones.get("FUNDAMENTOS"),
    )

    normativa = extract_normativa(
        parsed.plain_text, parsed.get_field("Normativa")
    )

    # Hash sobre el texto completo de la resolución (no solo cuerpo,
    # también cabecera: cualquier corrección posterior del DYCTEA en
    # metadatos debe disparar reingesta).
    digest = hashlib.sha256(parsed.plain_text.encode("utf-8")).hexdigest()

    return ResolucionTEAC(
        numero=numero.canonical,
        organo=organo,
        sede=sede,
        fecha=fecha,
        tipo=tipo,
        sentido=sentido,
        impuesto=impuesto,
        asunto=asunto[:300],
        criterio=criterio,
        criterio_confidence=CriterioConfidence.AUTO,
        normativa=normativa,
        resolucion_texto=parsed.plain_text[:60000],  # límite de seguridad.
        url=source_url,
        content_hash=digest,
        last_fetched_at=today,
    )


def process_numero(
    raw_numero: str,
    *,
    client: TeacClient,
    root_dir: Path,
    today: date,
    persist: bool = True,
) -> ResolucionOutcome:
    """Procesa un número. No lanza: errores van a `outcome.error`."""
    try:
        numero = parse_numero_reclamacion(raw_numero)
    except NumeroReclamacionParseError as exc:
        return ResolucionOutcome(
            numero=raw_numero,
            resolucion=None,
            persisted=None,
            error=f"número inválido: {exc}",
        )

    try:
        html = client.fetch_full(numero)
    except TeacFetchError as exc:
        return ResolucionOutcome(
            numero=numero.canonical,
            resolucion=None,
            persisted=None,
            error=f"fetch falló: {exc}",
        )

    try:
        parsed = parse_resolucion_html(html)
    except ResolucionParseError as exc:
        return ResolucionOutcome(
            numero=numero.canonical,
            resolucion=None,
            persisted=None,
            error=f"parse falló: {exc}",
        )

    try:
        resolucion = _build_resolucion(
            numero, parsed, today=today, source_url=None
        )
    except ResolucionParseError as exc:
        return ResolucionOutcome(
            numero=numero.canonical,
            resolucion=None,
            persisted=None,
            error=f"construcción falló: {exc}",
        )

    persisted = (
        persist_resolucion(resolucion, root=root_dir) if persist else None
    )
    return ResolucionOutcome(
        numero=numero.canonical,
        resolucion=resolucion,
        persisted=persisted,
        error=None,
    )


def run_ingest_for_numeros(
    numeros: list[str],
    *,
    client: TeacClient,
    root_dir: Path,
    today: date,
    persist: bool = True,
) -> IngestionReport:
    """Procesa una lista de números y devuelve el reporte agregado."""
    report = IngestionReport(today=today)
    for raw in numeros:
        report.outcomes.append(
            process_numero(
                raw,
                client=client,
                root_dir=root_dir,
                today=today,
                persist=persist,
            )
        )
    return report


def tipo_breakdown(report: IngestionReport) -> dict[str, int]:
    """Conteo por tipo de resolución (unifica criterio / extiende / ordinaria).

    El tipo es la dimensión más relevante doctrinalmente, así que se
    muestra prominente en el resumen del PR.
    """
    counts: dict[str, int] = {t.value: 0 for t in TipoResolucion}
    for outcome in report.accepted:
        if outcome.resolucion is not None:
            counts[outcome.resolucion.tipo.value] += 1
    return counts


def impuesto_breakdown(report: IngestionReport) -> dict[str, int]:
    """Conteo por impuesto (reutiliza enum de DGT)."""
    counts: dict[str, int] = {i.value: 0 for i in Impuesto}
    for outcome in report.accepted:
        if outcome.resolucion is not None:
            counts[outcome.resolucion.impuesto.value] += 1
    return counts
