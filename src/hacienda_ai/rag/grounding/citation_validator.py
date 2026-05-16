"""Valida que las citas de la respuesta del LLM apuntan al contexto RAG.

Verifica tres niveles:

1. **Referencias `[FUENTE N]`**: cada N debe estar entre las fuentes
   provistas en `BuiltContext`. Una referencia a `[FUENTE 99]` cuando
   solo entregamos 12 fuentes es alucinación.

2. **Citas normativas literales** (BOE-A, art. X.Y.Z, V0123-24, ECLI):
   se delegan al `citation_guard` existente para verificación contra
   el corpus auditable. Aquí añadimos una restricción extra: cada
   cita debe estar PRESENTE en alguno de los chunks recuperados (no
   solo "en el corpus" en general). Si el LLM cita un artículo que
   no aparece en NINGUNA fuente del contexto, es alucinación aunque
   el artículo exista realmente.

3. **Citas a normativa derogada en la fecha de devengo**: se cruza
   con el `TemporalFilterReport` (si se pasa) para detectar citas a
   chunks que el filtro había rechazado. Defensa en profundidad
   contra prompts que se las arreglen para colar normativa fuera de
   vigencia.

Niveles del veredicto:
- `safe`: todas las citas verificables y pertenecen a chunks
  recuperados.
- `warn`: hay citas no verificables pero plausibles (jurisprudencia
  con identificador BOE-A pero sin chunk en contexto — puede ser
  conocimiento general que aceptamos con disclaimer).
- `block`: hay citas inventadas (`[FUENTE N]` inexistente o cita
  normativa que NO aparece en ningún chunk del contexto). El
  orchestrator debe rechazar la respuesta y pedir al LLM que reescriba.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from ...irpf.scales import TaxScale
from ...models import Deduction, NormaRegistry
from ...safety.citation_guard import (
    Citation,
    CitationCheckResult,
    verify_citations,
)
from ..temporal import TemporalFilterReport
from .context_builder import BuiltContext


class GroundingVerdictLevel(str, Enum):
    """Resultado del validador."""

    SAFE = "safe"
    WARN = "warn"
    BLOCK = "block"


@dataclass(frozen=True)
class CitationIssue:
    """Problema detectado en una cita de la respuesta del LLM."""

    level: GroundingVerdictLevel
    text: str
    reason: str


@dataclass
class GroundingVerdict:
    """Veredicto completo + lista de problemas."""

    level: GroundingVerdictLevel
    issues: list[CitationIssue] = field(default_factory=list)
    cited_source_indices: set[int] = field(default_factory=set)
    citation_guard_report: CitationCheckResult | None = None

    def add(self, issue: CitationIssue) -> None:
        self.issues.append(issue)
        # Promovemos el nivel global al peor encontrado.
        if issue.level == GroundingVerdictLevel.BLOCK:
            self.level = GroundingVerdictLevel.BLOCK
        elif (
            issue.level == GroundingVerdictLevel.WARN
            and self.level == GroundingVerdictLevel.SAFE
        ):
            self.level = GroundingVerdictLevel.WARN


_RE_FUENTE_REF = re.compile(r"\[FUENTE\s+(\d{1,3})\]", re.IGNORECASE)


def validate_grounded_response(
    response_text: str,
    *,
    context: BuiltContext,
    registry: NormaRegistry | None = None,
    deductions: list[Deduction] | None = None,
    scales: list[TaxScale] | None = None,
    temporal_report: TemporalFilterReport | None = None,
) -> GroundingVerdict:
    """Valida la respuesta del LLM contra el contexto RAG.

    Argumentos:
    - `response_text`: texto generado por el LLM.
    - `context`: `BuiltContext` que se entregó al LLM. Usado para:
        * Verificar que `[FUENTE N]` apunta a N ∈ [1, len(sources)].
        * Verificar que las citas literales aparecen en algún chunk.
    - `registry`/`deductions`/`scales`: pasados a `citation_guard.verify_text`
      para la verificación canónica contra el corpus.
    - `temporal_report`: si se pasa, cualquier cita que apunte a un
      chunk RECHAZADO por el filtro temporal es BLOCK.
    """
    verdict = GroundingVerdict(level=GroundingVerdictLevel.SAFE)

    # 1. Referencias [FUENTE N].
    valid_indices = set(context.source_ids_by_index.keys())
    for match in _RE_FUENTE_REF.finditer(response_text):
        try:
            n = int(match.group(1))
        except ValueError:
            continue
        if n in valid_indices:
            verdict.cited_source_indices.add(n)
        else:
            verdict.add(
                CitationIssue(
                    level=GroundingVerdictLevel.BLOCK,
                    text=match.group(0),
                    reason=(
                        f"[FUENTE {n}] no existe en el contexto entregado "
                        f"(solo hay {len(valid_indices)} fuentes)."
                    ),
                )
            )

    # 2. Citas literales: delegamos al citation_guard existente para
    # extraer y normalizar las citas, luego verificamos cobertura.
    cg_report = verify_citations(
        response_text,
        corpus=deductions or [],
        scales=scales or [],
        registry=registry,
    )
    verdict.citation_guard_report = cg_report

    # Compilamos los textos de las fuentes para búsqueda de cobertura.
    source_texts = [s.body.lower() for s in context.sources]
    source_metadata_texts = [
        " ".join(line.lower() for line in s.metadata_lines)
        for s in context.sources
    ]

    # Construimos un índice de issues del citation_guard por la cita
    # afectada para clasificar el nivel rápidamente.
    cg_issues_by_raw: dict[str, str] = {}
    for cg_issue in cg_report.issues:
        cg_issues_by_raw[cg_issue.citation.raw.lower()] = cg_issue.level

    for citation in cg_report.citations:
        if _citation_appears_in_context(
            citation, source_texts, source_metadata_texts
        ):
            continue
        # Cita ausente del contexto entregado al LLM.
        guard_level = cg_issues_by_raw.get(citation.raw.lower())
        if guard_level == "blocking":
            # El guard ya la había marcado como bloqueante (no existe
            # en corpus, vigencia inconsistente, etc.) y además no está
            # en el contexto: doble inválida.
            verdict.add(
                CitationIssue(
                    level=GroundingVerdictLevel.BLOCK,
                    text=citation.raw,
                    reason=(
                        f"cita {citation.raw!r} bloqueada por citation_guard "
                        "y ausente del contexto entregado al LLM."
                    ),
                )
            )
        else:
            # Cita "safe" para el guard (existe en corpus general) pero
            # no aparece en las fuentes recuperadas: el LLM la conoce
            # por su entrenamiento, no por el contexto. Lo marcamos
            # como warn — el chat orchestrator decide si reescribir.
            verdict.add(
                CitationIssue(
                    level=GroundingVerdictLevel.WARN,
                    text=citation.raw,
                    reason=(
                        f"cita {citation.raw!r} no aparece en las fuentes "
                        "recuperadas; el LLM debe citar solo el contexto "
                        "entregado."
                    ),
                )
            )

    # 3. Cruce con TemporalFilterReport: ¿el LLM cita chunks que el
    # filtro temporal había rechazado por vigencia? Esto es un BLOCK
    # duro.
    if temporal_report is not None:
        rejected_ids = {m.chunk.chunk_id for m, _ in temporal_report.rejected}
        for source in context.sources:
            if source.chunk_id in rejected_ids:
                # No debería estar en el contexto si el filtro lo
                # rechazó; defendemos contra el caso de que el caller
                # no haya re-aplicado el filtro.
                verdict.add(
                    CitationIssue(
                        level=GroundingVerdictLevel.BLOCK,
                        text=f"[FUENTE {source.index}]",
                        reason=(
                            f"chunk {source.chunk_id!r} fue rechazado por el "
                            "filtro temporal pero apareció en el contexto. "
                            "No se debe citar."
                        ),
                    )
                )

    return verdict


def _citation_appears_in_context(
    citation: Citation,
    source_texts: list[str],
    source_metadata_texts: list[str],
) -> bool:
    """¿Esta cita está presente en alguno de los chunks o sus metadata?

    Comprueba dos anclas:
    1. El texto literal de la cita (`citation.raw`).
    2. El `boe_id` resuelto por el guard, si lo tiene.

    La cita "art. 19 LIRPF" debería aparecer porque (a) el texto del
    chunk de la LIRPF contiene "art. 19", y (b) los metadata del chunk
    llevan `BOE-A-2006-20764` (resolución del alias). Buscamos ambos
    para minimizar falsos negativos por variantes tipográficas.
    """
    needles = []
    raw_lower = citation.raw.lower().strip()
    if raw_lower:
        needles.append(raw_lower)
    if citation.boe_id:
        needles.append(citation.boe_id.lower())
    if not needles:
        return False
    for body in source_texts:
        if any(n in body for n in needles):
            return True
    for meta in source_metadata_texts:
        if any(n in meta for n in needles):
            return True
    return False
