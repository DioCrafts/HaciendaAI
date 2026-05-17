"""Capa de seguridad anti-alucinación: verificación de citas legales.

Antes de exponer cualquier respuesta generada por un LLM, este verificador
escanea el texto buscando todas las referencias normativas (BOE-A, boletines
autonómicos, artículos, leyes con número/año, alias como LIRPF/LIS/LIVA y
jurisprudencia TS/TEAC/DGT) y las cruza contra el corpus auditable:

- `NormaRegistry`: ¿existe la norma citada? ¿está vigente en la fecha del
  devengo? Una norma derogada o declarada inconstitucional en esa fecha
  bloquea la respuesta.
- Corpus de deducciones + escalas: para cada artículo citado asociado a una
  norma del corpus, ¿existe algún `Source` documentado con ese
  `boe_id + article`? Si no, es una cita potencialmente alucinada.
- Jurisprudencia / doctrina administrativa: hoy no hay corpus indexado de
  STS/TEAC/DGT, así que cualquier cita de esa familia se marca como
  `warning` honesto, no como `safe`.

El módulo no es responsable de validar afirmaciones sin cita explícita —
para eso hace falta otra capa de razonamiento sobre el contenido. Aquí solo
verificamos lo que el texto pone en negro sobre blanco.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from ..irpf.scales import TaxScale
from ..models import Deduction, NormaRegistry, NormaStatus, Source

Verdict = Literal["safe", "warn", "block"]
IssueLevel = Literal["blocking", "warning"]
CitationKind = Literal[
    "boe_state",
    "boe_regional",
    "article",
    "law_reference",
    "alias",
    "jurisprudence",
]

# Alias coloquiales de normas habituales. Permiten resolver "LIRPF", "Ley
# 35/2006", "LIS" a su BOE-A canónico para cruzarlo contra el registry y
# contra el corpus. La lista es deliberadamente corta y conservadora: solo
# alias bien establecidos que aparecen en doctrina y manuales AEAT. Cualquier
# alias sin entrada en el `NormaRegistry` se marcará igualmente como
# `warning` por no ser verificable, así que añadir uno aquí no genera
# falsos positivos de `safe`.
_ALIASES: dict[str, str] = {
    "lirpf": "BOE-A-2006-20764",
    "ley 35/2006": "BOE-A-2006-20764",
    "lis": "BOE-A-2014-12328",
    "ley 27/2014": "BOE-A-2014-12328",
    "liva": "BOE-A-1992-28740",
    "ley 37/1992": "BOE-A-1992-28740",
    "lgt": "BOE-A-2003-23186",
    "ley 58/2003": "BOE-A-2003-23186",
    "lisd": "BOE-A-1987-28141",
    "ley 29/1987": "BOE-A-1987-28141",
    "lip": "BOE-A-1991-14392",
    "ley 19/1991": "BOE-A-1991-14392",
    "ley 49/2002": "BOE-A-2002-25039",
}

_REGIONAL_PREFIXES = (
    "BOCM",
    "DOGC",
    "DOCV",
    "BOJA",
    "BOPV",
    "BOB",
    "BOG",
    "BOTHA",
    "BON",
    "DOG",
    "BOC",
    "BORM",
    "BOIB",
    "BOCYL",
    "DOCM",
    "BOPA",
    "DOE",
    "BOR",
    "BOA",
)

_RE_BOE_STATE = re.compile(r"\bBOE-A-(\d{4})-(\d+)\b")
_RE_BOE_REGIONAL = re.compile(
    r"\b(?:" + "|".join(_REGIONAL_PREFIXES) + r")-(\d{4})-(\d+)\b"
)
# Artículos: cubre "art. 57", "artículo 57", "art 57", "Art. 81 bis 2",
# "art. 23.2", "Art. 81 BIS". El segundo apartado (paragraph) y el sufijo
# (bis/ter/...) son opcionales.
_RE_ARTICLE = re.compile(
    r"\b[Aa]rt(?:[íi]culo|\.|\s)\s*(\d+)\s*"
    r"(bis|ter|quater|quinquies|sexies)?\s*"
    r"(?:\.\s*(\d+))?",
    re.IGNORECASE,
)
_RE_LAW = re.compile(
    r"\b(Ley|Real\s+Decreto-?[Ll]ey|Real\s+Decreto|RDL|RDLeg|RD)\s+(\d+)\s*/\s*(\d{4})\b",
    re.IGNORECASE,
)
_RE_ALIAS = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in sorted(_ALIASES, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)
_RE_JURISPRUDENCE = re.compile(
    r"\b(STS|STC|SAN|STSJ|TEAC|DGT|TJUE|consulta\s+DGT)\b"
    r"(?:\s+(V?\d+(?:[\-/]\d{2,4})?))?",
    re.IGNORECASE,
)

# Año "razonable" para un identificador BOE-A. Permitimos un año futuro como
# margen (la norma puede haberse publicado a finales del año previo al fiscal
# que cubre). Cualquier cosa más allá la consideramos inventada.
_BOE_YEAR_MIN = 1960
_BOE_YEAR_MAX_OFFSET = 1  # current year + 1

# Ventana en caracteres usada para asociar un artículo a la norma más cercana
# que lo precede o le sigue en el texto. ~80 chars cubre frases como
# "según el art. 57 LIRPF" o "art. 81 bis de la Ley 35/2006".
_ASSOCIATION_WINDOW = 80


@dataclass(frozen=True)
class Citation:
    """Una cita extraída del texto.

    `boe_id` se rellena tras la resolución (BOE-A directo, alias o asociación
    por proximidad). Una `Citation` puede tener `boe_id=None` (artículo
    huérfano que no se ha podido asociar a ninguna norma) — en ese caso el
    verificador la marca como `warning` por no poder cruzarla.
    """

    kind: CitationKind
    raw: str
    span: tuple[int, int]
    boe_id: str | None = None
    article: str | None = None
    article_suffix: str | None = None
    paragraph: str | None = None
    law_label: str | None = None
    juris_kind: str | None = None
    juris_ref: str | None = None


@dataclass(frozen=True)
class CitationIssue:
    level: IssueLevel
    code: str
    message: str
    citation: Citation


@dataclass(frozen=True)
class CitationCheckResult:
    verdict: Verdict
    citations: tuple[Citation, ...]
    issues: tuple[CitationIssue, ...]

    @property
    def is_safe(self) -> bool:
        return self.verdict == "safe"

    @property
    def has_blocks(self) -> bool:
        return any(i.level == "blocking" for i in self.issues)

    @property
    def blocking_issues(self) -> tuple[CitationIssue, ...]:
        return tuple(i for i in self.issues if i.level == "blocking")

    @property
    def warnings(self) -> tuple[CitationIssue, ...]:
        return tuple(i for i in self.issues if i.level == "warning")


def _normalize_alias(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _resolve_alias(text: str) -> str | None:
    return _ALIASES.get(_normalize_alias(text))


def extract_citations(text: str) -> list[Citation]:
    """Extrae todas las citas reconocidas del texto.

    El orden es semánticamente irrelevante; lo dejamos por posición para
    facilitar la asociación artículo→norma por proximidad. Las
    superposiciones se evitan emitiendo BOE-A primero y descartando luego
    los matches de leyes que ya estén cubiertos por un BOE-A en la misma
    posición.
    """
    citations: list[Citation] = []
    covered: list[tuple[int, int]] = []

    def _is_covered(span: tuple[int, int]) -> bool:
        return any(start <= span[0] and span[1] <= end for start, end in covered)

    for m in _RE_BOE_STATE.finditer(text):
        span = m.span()
        citations.append(
            Citation(
                kind="boe_state",
                raw=m.group(0),
                span=span,
                boe_id=m.group(0),
            )
        )
        covered.append(span)

    for m in _RE_BOE_REGIONAL.finditer(text):
        span = m.span()
        if _is_covered(span):
            continue
        citations.append(
            Citation(
                kind="boe_regional",
                raw=m.group(0),
                span=span,
                boe_id=m.group(0),
            )
        )
        covered.append(span)

    for m in _RE_ALIAS.finditer(text):
        span = m.span()
        if _is_covered(span):
            continue
        boe_id = _resolve_alias(m.group(1))
        citations.append(
            Citation(
                kind="alias",
                raw=m.group(0),
                span=span,
                boe_id=boe_id,
                law_label=m.group(1),
            )
        )
        covered.append(span)

    for m in _RE_LAW.finditer(text):
        span = m.span()
        if _is_covered(span):
            continue
        label = f"{m.group(1)} {m.group(2)}/{m.group(3)}"
        normalized = _normalize_alias(f"ley {m.group(2)}/{m.group(3)}")
        boe_id = _ALIASES.get(normalized)
        citations.append(
            Citation(
                kind="law_reference",
                raw=m.group(0),
                span=span,
                boe_id=boe_id,
                law_label=label,
            )
        )
        covered.append(span)

    for m in _RE_ARTICLE.finditer(text):
        span = m.span()
        if _is_covered(span):
            continue
        citations.append(
            Citation(
                kind="article",
                raw=m.group(0),
                span=span,
                article=m.group(1),
                article_suffix=m.group(2).lower() if m.group(2) else None,
                paragraph=m.group(3),
            )
        )

    for m in _RE_JURISPRUDENCE.finditer(text):
        span = m.span()
        if _is_covered(span):
            continue
        citations.append(
            Citation(
                kind="jurisprudence",
                raw=m.group(0),
                span=span,
                juris_kind=m.group(1).upper().replace(" ", "_"),
                juris_ref=m.group(2),
            )
        )

    citations.sort(key=lambda c: c.span[0])
    return citations


def _associate_articles_with_normas(citations: list[Citation]) -> list[Citation]:
    """Asocia cada artículo a la norma BOE-A/alias más cercana en el texto.

    Devuelve una nueva lista en la que los `Citation` de tipo `article`
    tienen rellenado `boe_id` cuando hay una norma con `boe_id != None`
    dentro de la ventana `_ASSOCIATION_WINDOW` antes o después del
    artículo. Las citas que no son artículos se devuelven sin cambios.
    """
    norma_carriers = [
        c
        for c in citations
        if c.boe_id is not None
        and c.kind in {"boe_state", "boe_regional", "alias", "law_reference"}
    ]
    out: list[Citation] = []
    for citation in citations:
        if citation.kind != "article":
            out.append(citation)
            continue
        nearest: Citation | None = None
        nearest_distance: int = _ASSOCIATION_WINDOW + 1
        center_article = (citation.span[0] + citation.span[1]) // 2
        for carrier in norma_carriers:
            center_carrier = (carrier.span[0] + carrier.span[1]) // 2
            distance = abs(center_article - center_carrier)
            if distance < nearest_distance:
                nearest_distance = distance
                nearest = carrier
        if nearest is not None and nearest_distance <= _ASSOCIATION_WINDOW:
            out.append(
                Citation(
                    kind=citation.kind,
                    raw=citation.raw,
                    span=citation.span,
                    boe_id=nearest.boe_id,
                    article=citation.article,
                    article_suffix=citation.article_suffix,
                    paragraph=citation.paragraph,
                    law_label=nearest.law_label,
                )
            )
        else:
            out.append(citation)
    return out


def _index_sources(
    corpus: list[Deduction] | None,
    scales: list[TaxScale] | None,
    extra_documented_sources: list[Source] | None = None,
) -> dict[str, set[tuple[str, str | None]]]:
    """Construye un índice `boe_id -> {(article_number, suffix), ...}` con todos
    los pinpoints documentados en el corpus. Una cita BOE-A + artículo se
    considera "documentada" si su (número, sufijo) está en ese conjunto.

    `extra_documented_sources` permite añadir pinpoints documentados que
    no figuran en el corpus principal de deducciones — por ejemplo, las
    `Source` del módulo IVA (`iva.iva_documented_sources()`) o de otros
    tributos que se modelen como módulos separados. Sin esto, una cita
    correcta a LIVA art. 90 sería marcada como `ARTICLE_NOT_IN_CORPUS`
    porque el corpus de deducciones IRPF no la incluye.
    """
    index: dict[str, set[tuple[str, str | None]]] = {}
    sources: list[Source] = []
    if corpus:
        for d in corpus:
            sources.extend(d.sources)
    if scales:
        for s in scales:
            sources.extend(s.sources)
    if extra_documented_sources:
        sources.extend(extra_documented_sources)
    for src in sources:
        if not src.boe_id or not src.article:
            continue
        norm = _parse_article(src.article)
        if norm is None:
            continue
        index.setdefault(src.boe_id, set()).add(norm)
    return index


def _parse_article(article: str) -> tuple[str, str | None] | None:
    """Convierte 'art. 57', 'art. 81 bis', 'artículo 19', 'a57', 'boe:da-4'
    a la tupla `(número, sufijo)`. Devuelve None si no es reconocible.
    """
    s = article.strip().lower()
    if s.startswith("boe:"):
        # Anclas BOE no numéricas (DA, DT, capítulos): las dejamos fuera del
        # cruce numérico. El verificador no las puede confirmar como
        # "artículo regular"; quedan como WARN si las cita el texto.
        return None
    s = re.sub(r"art[íi]culo|art\.|art", "", s).strip()
    m = re.match(r"^(\d+)\s*(bis|ter|quater|quinquies|sexies)?", s)
    if not m:
        return None
    return (m.group(1), m.group(2))


def _boe_year_is_suspicious(boe_id: str) -> bool:
    m = re.match(r"BOE-A-(\d{4})-\d+", boe_id)
    if not m:
        return True
    year = int(m.group(1))
    current_year = date.today().year
    return year < _BOE_YEAR_MIN or year > current_year + _BOE_YEAR_MAX_OFFSET


def verify_citations(
    text: str,
    *,
    corpus: list[Deduction] | None = None,
    scales: list[TaxScale] | None = None,
    registry: NormaRegistry | None = None,
    devengo: date | None = None,
    extra_documented_sources: list[Source] | None = None,
) -> CitationCheckResult:
    """Verifica todas las citas detectadas en `text`.

    `devengo` se usa para evaluar la vigencia de las normas en esa fecha. Si
    no se proporciona, se evalúa contra `date.today()` con un mensaje
    explícito en el motivo si una norma resulta derogada.

    `extra_documented_sources` añade pinpoints documentados al índice
    de verificación. Útil para tributos modelados en módulos separados
    (`iva`, `is`, …) que no figuran en el `corpus` principal de
    deducciones IRPF pero cuyas citas el LLM puede emitir legítimamente.
    """
    citations = _associate_articles_with_normas(extract_citations(text))
    sources_index = _index_sources(corpus, scales, extra_documented_sources)
    devengo_eff = devengo or date.today()
    issues: list[CitationIssue] = []

    for citation in citations:
        if citation.kind == "jurisprudence":
            issues.append(
                CitationIssue(
                    level="warning",
                    code="JURISPRUDENCE_NOT_INDEXED",
                    message=(
                        f"Cita a {citation.juris_kind}"
                        + (f" {citation.juris_ref}" if citation.juris_ref else "")
                        + " sin corpus de jurisprudencia indexado: el motor "
                        "no puede verificar su existencia ni vigencia."
                    ),
                    citation=citation,
                )
            )
            continue

        if citation.kind == "boe_regional":
            # No tenemos verificador para boletines autonómicos todavía.
            issues.append(
                CitationIssue(
                    level="warning",
                    code="REGIONAL_BULLETIN_NOT_VERIFIABLE",
                    message=(
                        f"Cita a boletín autonómico {citation.boe_id}: el "
                        "verificador BOE solo cubre el boletín estatal; la "
                        "cita queda sin contrastar."
                    ),
                    citation=citation,
                )
            )
            continue

        if citation.kind in {"boe_state", "alias", "law_reference"}:
            boe_id = citation.boe_id
            if citation.kind == "law_reference" and boe_id is None:
                issues.append(
                    CitationIssue(
                        level="warning",
                        code="LAW_REFERENCE_UNRESOLVED",
                        message=(
                            f"Cita a «{citation.raw}» sin mapeo a BOE-A: el "
                            "verificador no puede confirmar que la norma "
                            "exista en el registro auditable."
                        ),
                        citation=citation,
                    )
                )
                continue
            if boe_id is None:
                # Alias sin mapeo conocido (no debería ocurrir con la lista
                # actual, pero defensivo).
                issues.append(
                    CitationIssue(
                        level="warning",
                        code="ALIAS_UNRESOLVED",
                        message=(
                            f"Alias «{citation.raw}» no reconocido por el "
                            "verificador."
                        ),
                        citation=citation,
                    )
                )
                continue
            if citation.kind == "boe_state" and _boe_year_is_suspicious(boe_id):
                issues.append(
                    CitationIssue(
                        level="blocking",
                        code="BOE_YEAR_OUT_OF_RANGE",
                        message=(
                            f"Identificador BOE-A {boe_id} fuera de rango "
                            "razonable: año imposible para una norma vigente."
                        ),
                        citation=citation,
                    )
                )
                continue
            if registry is not None and not registry.knows(boe_id):
                issues.append(
                    CitationIssue(
                        level="warning",
                        code="NORMA_NOT_REGISTERED",
                        message=(
                            f"La norma {boe_id} no está en el registro "
                            "auditable; la cita no puede contrastarse."
                        ),
                        citation=citation,
                    )
                )
                continue
            if registry is not None:
                version = registry.version_at(boe_id, devengo_eff)
                if version is None:
                    issues.append(
                        CitationIssue(
                            level="warning",
                            code="NORMA_VERSION_UNKNOWN_AT_DATE",
                            message=(
                                f"No consta versión registrada de {boe_id} "
                                f"en {devengo_eff.isoformat()}."
                            ),
                            citation=citation,
                        )
                    )
                elif version.status in {
                    NormaStatus.DEROGADA,
                    NormaStatus.INCONSTITUCIONAL,
                }:
                    issues.append(
                        CitationIssue(
                            level="blocking",
                            code=f"NORMA_{version.status.value.upper()}",
                            message=(
                                f"La norma {boe_id} estaba "
                                f"{version.status.value} en "
                                f"{devengo_eff.isoformat()}; citarla como "
                                "vigente es incorrecto."
                            ),
                            citation=citation,
                        )
                    )
                elif version.status == NormaStatus.SUSPENDIDA:
                    issues.append(
                        CitationIssue(
                            level="warning",
                            code="NORMA_SUSPENDIDA",
                            message=(
                                f"La norma {boe_id} estaba suspendida en "
                                f"{devengo_eff.isoformat()}; revisar la "
                                "aplicación antes de afirmar vigencia."
                            ),
                            citation=citation,
                        )
                    )
            continue

        if citation.kind == "article":
            boe_id = citation.boe_id
            if boe_id is None:
                issues.append(
                    CitationIssue(
                        level="warning",
                        code="ARTICLE_ORPHAN",
                        message=(
                            f"Artículo «{citation.raw}» sin norma asociada "
                            "en el contexto: no se puede verificar."
                        ),
                        citation=citation,
                    )
                )
                continue
            if registry is not None and not registry.knows(boe_id):
                # Ya cubierto por la cita de la norma; aquí solo warning suave.
                issues.append(
                    CitationIssue(
                        level="warning",
                        code="ARTICLE_PARENT_NOT_REGISTERED",
                        message=(
                            f"Artículo «{citation.raw}» referenciado a "
                            f"{boe_id}, norma no registrada."
                        ),
                        citation=citation,
                    )
                )
                continue
            documented = sources_index.get(boe_id, set())
            key = (citation.article or "", citation.article_suffix)
            if not documented:
                # La norma existe en el registry pero no figura en el corpus
                # con detalle articular. Es un WARN razonable: el artículo
                # puede ser válido pero no está documentado en nuestro
                # corpus auditable.
                issues.append(
                    CitationIssue(
                        level="warning",
                        code="NORMA_HAS_NO_INDEXED_ARTICLES",
                        message=(
                            f"La norma {boe_id} no tiene artículos "
                            "indexados en el corpus; la cita del "
                            f"artículo {citation.article}"
                            f"{(' ' + citation.article_suffix) if citation.article_suffix else ''} "
                            "no es contrastable."
                        ),
                        citation=citation,
                    )
                )
                continue
            if key not in documented:
                issues.append(
                    CitationIssue(
                        level="blocking",
                        code="ARTICLE_NOT_IN_CORPUS",
                        message=(
                            f"El artículo {citation.article}"
                            f"{(' ' + citation.article_suffix) if citation.article_suffix else ''} "
                            f"no está documentado en {boe_id} dentro del "
                            "corpus auditable. Posible cita alucinada."
                        ),
                        citation=citation,
                    )
                )

    verdict: Verdict = "safe"
    if any(i.level == "blocking" for i in issues):
        verdict = "block"
    elif issues:
        verdict = "warn"

    return CitationCheckResult(
        verdict=verdict,
        citations=tuple(citations),
        issues=tuple(issues),
    )
