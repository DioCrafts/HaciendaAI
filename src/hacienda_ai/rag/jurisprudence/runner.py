"""Orquestador del pipeline de ingesta CENDOJ.

Para cada ECLI solicitado:

1. Resuelve a HTML completo vía `CendojClient` (local o HTTP).
2. Parsea el HTML con `parse_sentencia_html` → estructura intermedia.
3. Clasifica por materia tributaria (`classify_sentencia`).
4. Si no es tributaria → se descarta y se registra el rechazo.
5. Si es tributaria → extrae fallo + ratio decidendi, calcula content_hash,
   ensambla `Sentencia` del modelo de dominio.
6. Persiste a `data/jurisprudencia/<organo>/<año>/<ECLI>.json`.

El runner es agnóstico del cliente: el caller decide si usa
`LocalCendojClient` (CI, operadores con archivos descargados) o
`HttpCendojClient` (experimental).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ...models import (
    FalloSentido,
    Organo,
    RatioConfidence,
    Sentencia,
)
from .client import CendojClient, CendojFetchError
from .ecli import ECLI, EcliParseError, organo_from_tribunal_codigo, parse_ecli
from .extractors import extract_fallo, extract_ratio_decidendi
from .parser import (
    ParsedSentencia,
    SentenciaParseError,
    parse_sentencia_date,
    parse_sentencia_html,
)
from .persistence import PersistedSentencia, persist_sentencia
from .tax_filter import TaxClassification, classify_sentencia


@dataclass(frozen=True)
class SentenciaOutcome:
    """Resultado del procesamiento de UN ECLI."""

    ecli: str
    sentencia: Sentencia | None
    classification: TaxClassification | None
    persisted: PersistedSentencia | None
    error: str | None

    @property
    def accepted(self) -> bool:
        return self.sentencia is not None

    @property
    def rejected(self) -> bool:
        return (
            self.classification is not None
            and not self.classification.accept
        )


@dataclass
class IngestionReport:
    """Resultado de procesar un lote de ECLIs."""

    today: date
    outcomes: list[SentenciaOutcome] = field(default_factory=list)

    @property
    def accepted(self) -> list[SentenciaOutcome]:
        return [o for o in self.outcomes if o.accepted]

    @property
    def rejected(self) -> list[SentenciaOutcome]:
        return [o for o in self.outcomes if o.rejected]

    @property
    def errored(self) -> list[SentenciaOutcome]:
        return [o for o in self.outcomes if o.error is not None]

    @property
    def newly_persisted(self) -> list[SentenciaOutcome]:
        return [
            o for o in self.outcomes
            if o.persisted is not None and o.persisted.was_new
        ]


def _build_sentencia(
    ecli: ECLI,
    parsed: ParsedSentencia,
    *,
    today: date,
    source_url: str | None,
) -> Sentencia:
    """Convierte una `ParsedSentencia` clasificada como fiscal en `Sentencia`.

    Aplica los extractores de fallo y ratio decidendi. El campo `ratio_decidendi`
    se marca SIEMPRE como `RatioConfidence.AUTO`: ningún extracto automático se
    considera doctrina validada hasta que un humano lo revise.
    """
    fecha_raw = parsed.get_field("Fecha")
    fecha = parse_sentencia_date(fecha_raw) if fecha_raw else None
    if fecha is None:
        # Sin fecha no podemos versionar. Caer es preferible a inventar.
        raise SentenciaParseError(
            f"{ecli.canonical}: cabecera sin campo Fecha parseable"
        )

    organo = organo_from_tribunal_codigo(ecli.tribunal_codigo)

    sala = parsed.get_field("Sala")
    organo_field = parsed.get_field("Órgano", "Organo")
    if sala is None and organo_field is not None:
        # CENDOJ a veces empotra la Sala dentro del campo "Órgano"
        # ("Tribunal Supremo. Sala de lo Contencioso"). La capturamos.
        sala_match = re.search(
            r"Sala\s+(?:de\s+lo\s+)?(\S+(?:\s+\S+)?)",
            organo_field,
            re.IGNORECASE,
        )
        if sala_match:
            sala = sala_match.group(0).strip()

    fallo_section = parsed.secciones.get("FALLO")
    fundamentos_section = parsed.secciones.get("FUNDAMENTOS_DE_DERECHO")

    fallo_sentido, fallo_texto = extract_fallo(parsed.plain_text, fallo_section)
    ratio = extract_ratio_decidendi(
        parsed.plain_text, fundamentos_section=fundamentos_section
    )

    if not fallo_texto:
        # Sin fallo no podemos persistir: el campo es obligatorio en el modelo.
        fallo_texto = "[no se pudo extraer texto del fallo]"
        fallo_sentido = FalloSentido.DESCONOCIDO

    digest = hashlib.sha256(parsed.plain_text.encode("utf-8")).hexdigest()

    return Sentencia(
        ecli=ecli.canonical,
        organo=organo,
        tribunal_codigo=ecli.tribunal_codigo,
        sala=sala,
        seccion=parsed.get_field("Sección", "Seccion"),
        fecha=fecha,
        ponente=parsed.get_field("Ponente"),
        numero_resolucion=parsed.get_field(
            "Nº de Resolución", "N de Resolucion", "Num de Resolucion"
        ),
        numero_recurso=parsed.get_field(
            "Nº de Recurso", "N de Recurso", "Num de Recurso"
        ),
        fallo_sentido=fallo_sentido,
        fallo_texto=fallo_texto,
        ratio_decidendi=ratio,
        ratio_confidence=RatioConfidence.AUTO,
        resumen=parsed.get_field("Materia"),
        url=source_url,
        content_hash=digest,
        last_fetched_at=today,
    )


def process_ecli(
    raw_ecli: str,
    *,
    client: CendojClient,
    root_dir: Path,
    today: date,
    persist: bool = True,
) -> SentenciaOutcome:
    """Procesa un único ECLI. No lanza: cualquier fallo va a `outcome.error`."""
    try:
        ecli = parse_ecli(raw_ecli)
    except EcliParseError as exc:
        return SentenciaOutcome(
            ecli=raw_ecli,
            sentencia=None,
            classification=None,
            persisted=None,
            error=f"ECLI inválido: {exc}",
        )

    try:
        html = client.fetch_full(ecli)
    except CendojFetchError as exc:
        return SentenciaOutcome(
            ecli=ecli.canonical,
            sentencia=None,
            classification=None,
            persisted=None,
            error=f"fetch falló: {exc}",
        )

    try:
        parsed = parse_sentencia_html(html)
    except SentenciaParseError as exc:
        return SentenciaOutcome(
            ecli=ecli.canonical,
            sentencia=None,
            classification=None,
            persisted=None,
            error=f"parse falló: {exc}",
        )

    classification = classify_sentencia(parsed)
    if not classification.accept:
        return SentenciaOutcome(
            ecli=ecli.canonical,
            sentencia=None,
            classification=classification,
            persisted=None,
            error=None,
        )

    try:
        sentencia = _build_sentencia(
            ecli, parsed, today=today, source_url=None
        )
    except (SentenciaParseError, EcliParseError) as exc:
        return SentenciaOutcome(
            ecli=ecli.canonical,
            sentencia=None,
            classification=classification,
            persisted=None,
            error=f"construcción falló: {exc}",
        )

    persisted = (
        persist_sentencia(sentencia, root=root_dir) if persist else None
    )
    return SentenciaOutcome(
        ecli=ecli.canonical,
        sentencia=sentencia,
        classification=classification,
        persisted=persisted,
        error=None,
    )


def run_ingest_for_eclis(
    eclis: list[str],
    *,
    client: CendojClient,
    root_dir: Path,
    today: date,
    persist: bool = True,
) -> IngestionReport:
    """Procesa una lista de ECLIs y devuelve el reporte agregado."""
    report = IngestionReport(today=today)
    for raw in eclis:
        report.outcomes.append(
            process_ecli(
                raw,
                client=client,
                root_dir=root_dir,
                today=today,
                persist=persist,
            )
        )
    return report


def organo_breakdown(report: IngestionReport) -> dict[str, int]:
    """Conteo de aceptadas por órgano (para resumen del PR)."""
    counts: dict[str, int] = {o.value: 0 for o in Organo}
    for outcome in report.accepted:
        if outcome.sentencia is not None:
            counts[outcome.sentencia.organo.value] += 1
    return counts
