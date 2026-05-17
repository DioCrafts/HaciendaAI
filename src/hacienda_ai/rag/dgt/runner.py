"""Orquestador del pipeline de ingesta DGT.

Para cada número de consulta solicitado:

1. Resuelve a HTML completo vía `DgtClient` (local o HTTP).
2. Parsea con `parse_consulta_html` → estructura intermedia.
3. Detecta impuesto principal, extrae normativa y criterio doctrinal.
4. Calcula `content_hash` SHA-256 de la contestación normalizada.
5. Ensambla `ConsultaDGT` del modelo de dominio.
6. Persiste a `data/dgt_consultas/<año>/V<NNNN>-<YY>.json`.

A diferencia del runner de jurisprudencia, AQUÍ NO HAY FILTRO FISCAL:
todas las consultas DGT son tributarias por definición. Sí detectamos
el impuesto para indexar / mostrar en el resumen del PR.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ...models import (
    ConsultaDGT,
    CriterioConfidence,
    Impuesto,
)
from .client import DgtClient, DgtFetchError
from .extractors import detect_impuesto, extract_criterio, extract_normativa
from .numero import (
    NumeroConsulta,
    NumeroConsultaParseError,
    parse_numero_consulta,
)
from .parser import (
    ConsultaParseError,
    ParsedConsulta,
    parse_consulta_date,
    parse_consulta_html,
)
from .persistence import PersistedConsulta, persist_consulta


@dataclass(frozen=True)
class ConsultaOutcome:
    """Resultado del procesamiento de UN número de consulta."""

    numero: str
    consulta: ConsultaDGT | None
    persisted: PersistedConsulta | None
    error: str | None

    @property
    def accepted(self) -> bool:
        return self.consulta is not None


@dataclass
class IngestionReport:
    today: date
    outcomes: list[ConsultaOutcome] = field(default_factory=list)

    @property
    def accepted(self) -> list[ConsultaOutcome]:
        return [o for o in self.outcomes if o.accepted]

    @property
    def errored(self) -> list[ConsultaOutcome]:
        return [o for o in self.outcomes if o.error is not None]

    @property
    def newly_persisted(self) -> list[ConsultaOutcome]:
        return [
            o for o in self.outcomes
            if o.persisted is not None and o.persisted.was_new
        ]


def _build_consulta(
    numero: NumeroConsulta,
    parsed: ParsedConsulta,
    *,
    today: date,
    source_url: str | None,
) -> ConsultaDGT:
    """Convierte una `ParsedConsulta` en `ConsultaDGT` del modelo de dominio."""
    fecha_salida_raw = parsed.get_field("Fecha Salida", "Fecha de Salida")
    fecha_salida = (
        parse_consulta_date(fecha_salida_raw) if fecha_salida_raw else None
    )
    if fecha_salida is None:
        raise ConsultaParseError(
            f"{numero.canonical}: cabecera sin Fecha Salida parseable"
        )

    fecha_entrada_raw = parsed.get_field("Fecha Entrada", "Fecha de Entrada")
    fecha_entrada = (
        parse_consulta_date(fecha_entrada_raw) if fecha_entrada_raw else None
    )

    asunto = (
        parsed.get_field("Asunto", "Materia")
        or _first_line_of(parsed.secciones.get("DESCRIPCION_HECHOS"))
        or "(sin asunto)"
    )

    cuestion = parsed.secciones.get("CUESTION_PLANTEADA") or ""
    contestacion = parsed.secciones.get("CONTESTACION_COMPLETA") or ""

    if not cuestion.strip() and not contestacion.strip():
        # Si no hay ni cuestión ni contestación, el HTML no es una
        # consulta DGT estándar; fallar es preferible a guardar basura.
        raise ConsultaParseError(
            f"{numero.canonical}: no se detectaron secciones canónicas "
            "(Cuestión Planteada / Contestación Completa)"
        )

    # Si una de las dos secciones falta, lo señalamos con un marcador para
    # que el revisor humano lo vea y decida (a veces Petete simplifica
    # consultas reincidentes).
    if not cuestion.strip():
        cuestion = "[sección Cuestión Planteada no detectada en el HTML]"
    if not contestacion.strip():
        contestacion = "[sección Contestación Completa no detectada en el HTML]"

    normativa_header = parsed.get_field("Normativa")
    normativa = extract_normativa(parsed.plain_text, normativa_header)

    impuesto = detect_impuesto(
        normativa=normativa_header,
        asunto=asunto,
        cuerpo=parsed.plain_text,
    )

    criterio = extract_criterio(
        parsed.plain_text, contestacion_section=contestacion
    )

    digest = hashlib.sha256(
        (cuestion + "\n" + contestacion).encode("utf-8")
    ).hexdigest()

    return ConsultaDGT(
        numero=numero.canonical,
        fecha_salida=fecha_salida,
        fecha_entrada=fecha_entrada,
        impuesto=impuesto,
        asunto=asunto,
        cuestion_planteada=cuestion,
        contestacion_completa=contestacion,
        criterio=criterio,
        criterio_confidence=CriterioConfidence.AUTO,
        normativa=normativa,
        url=source_url,
        content_hash=digest,
        last_fetched_at=today,
    )


def _first_line_of(text: str | None) -> str | None:
    if not text:
        return None
    for line in text.split("\n"):
        line = line.strip()
        # Saltamos el encabezado de la sección ("Descripción Hechos:").
        if line and not line.lower().startswith("descripci"):
            return line[:200]
    return None


def process_numero(
    raw_numero: str,
    *,
    client: DgtClient,
    root_dir: Path,
    today: date,
    persist: bool = True,
) -> ConsultaOutcome:
    """Procesa un único número de consulta. No lanza: errores van a `outcome.error`."""
    try:
        numero = parse_numero_consulta(raw_numero)
    except NumeroConsultaParseError as exc:
        return ConsultaOutcome(
            numero=raw_numero,
            consulta=None,
            persisted=None,
            error=f"número inválido: {exc}",
        )

    try:
        html = client.fetch_full(numero)
    except DgtFetchError as exc:
        return ConsultaOutcome(
            numero=numero.canonical,
            consulta=None,
            persisted=None,
            error=f"fetch falló: {exc}",
        )

    try:
        parsed = parse_consulta_html(html)
    except ConsultaParseError as exc:
        return ConsultaOutcome(
            numero=numero.canonical,
            consulta=None,
            persisted=None,
            error=f"parse falló: {exc}",
        )

    try:
        consulta = _build_consulta(
            numero, parsed, today=today, source_url=None
        )
    except ConsultaParseError as exc:
        return ConsultaOutcome(
            numero=numero.canonical,
            consulta=None,
            persisted=None,
            error=f"construcción falló: {exc}",
        )

    persisted = persist_consulta(consulta, root=root_dir) if persist else None
    return ConsultaOutcome(
        numero=numero.canonical,
        consulta=consulta,
        persisted=persisted,
        error=None,
    )


def run_ingest_for_numeros(
    numeros: list[str],
    *,
    client: DgtClient,
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


def impuesto_breakdown(report: IngestionReport) -> dict[str, int]:
    """Conteo de consultas aceptadas por impuesto (para resumen del PR)."""
    counts: dict[str, int] = {i.value: 0 for i in Impuesto}
    for outcome in report.accepted:
        if outcome.consulta is not None:
            counts[outcome.consulta.impuesto.value] += 1
    return counts
