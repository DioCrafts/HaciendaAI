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
- `JurisprudenceRegistry` (opcional): cruza ECLI, número canónico DGT
  (`V0123-24`) y número canónico TEAC (`00/12345/2023`) contra el
  corpus indexado de sentencias / consultas vinculantes / resoluciones
  TEAC. Si la cita es un identificador canónico y NO existe en el
  corpus → `block` (cita potencialmente alucinada). Si es una
  referencia jurisprudencial sin identificador canónico (p.ej.,
  "STS 1234/2020") → `warn` ambigua: el motor no puede verificar y
  el LLM debería citar con ECLI/numero canónico para ser auditable.
  Sin registry inyectado, las citas jurisprudenciales se quedan en
  `warn` (corpus no indexado) — comportamiento previo.

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
from .jurisprudence_registry import JurisprudenceRegistry

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
# Identificador ECLI canónico (European Case Law Identifier). Aceptamos el
# formato español `ECLI:ES:<tribunal>:<año>:<id>` con sufijos opcionales
# (`.S2`, `:S2`). Solo ECLIs estructuralmente válidos: el verificador
# decidirá si existen en el corpus de jurisprudencia.
_RE_ECLI = re.compile(
    r"\bECLI:ES:[A-Z]+[A-Z0-9]*:\d{4}:[A-Z0-9.]+(?:[:.][A-Z0-9]+)?\b",
    re.IGNORECASE,
)
# Número canónico de consulta DGT vinculante: V<NNNN>-<YY|YYYY>. La 'V'
# se exige (las no vinculantes empiezan por C y no están en corpus).
# `(?<![A-Z])` evita capturar partes de otras palabras como "PROV0123-24".
_RE_DGT_CANONICAL = re.compile(
    r"(?<![A-Za-z])V\d{1,5}-\d{2,4}\b",
)
# Número canónico de resolución TEAC: DD/NNNNN/AAAA con sufijos opcionales.
# Forma corta R.G. también aceptada. Para evitar capturar fechas, exigimos
# que el "AAAA" tenga 4 dígitos cuando va sin DD (forma R.G.). Cuando va
# con DD/NNNNN/AAAA, AAAA puede ser de 2 o 4 dígitos.
_RE_TEAC_CANONICAL = re.compile(
    r"\b\d{1,2}/\d{1,7}/\d{4}(?:/\d{1,3})?(?:/\d{1,3})?\b"
)
_RE_TEAC_RG = re.compile(
    r"\bR\.?\s*G\.?[\.:/\s]+\d{1,7}\s*/\s*\d{2,4}\b",
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

    `juris_kind` distingue la familia de jurisprudencia/doctrina cuando
    `kind == "jurisprudence"`: `ECLI`, `DGT`, `TEAC`, o las formas
    ambiguas (`STS`, `STC`, `SAN`, `STSJ`, `TJUE`, `CONSULTA_DGT`).
    `juris_canonical` contiene el identificador en forma canónica para
    lookup directo en el `JurisprudenceRegistry` cuando la cita lo
    permite (ECLI completo, `V0123-24`, `00/12345/2023`). Cuando la
    cita es ambigua (p.ej. "STS 1234/2020") `juris_canonical` queda en
    `None` y el verificador la marca como `warn` ambiguo.
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
    juris_canonical: str | None = None


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

    # Identificadores canónicos verificables: ECLI, DGT V0123-24,
    # TEAC 00/12345/2023, TEAC R.G. NNNN/YYYY. Se emiten ANTES del
    # regex genérico de jurisprudencia y se "cubren" para que el
    # genérico no los duplique.
    for m in _RE_ECLI.finditer(text):
        span = m.span()
        if _is_covered(span):
            continue
        raw_ecli = m.group(0)
        citations.append(
            Citation(
                kind="jurisprudence",
                raw=raw_ecli,
                span=span,
                juris_kind="ECLI",
                juris_ref=raw_ecli,
                juris_canonical=raw_ecli.upper(),
            )
        )
        covered.append(span)

    for m in _RE_DGT_CANONICAL.finditer(text):
        span = m.span()
        if _is_covered(span):
            continue
        raw_v = m.group(0)
        citations.append(
            Citation(
                kind="jurisprudence",
                raw=raw_v,
                span=span,
                juris_kind="DGT",
                juris_ref=raw_v,
                juris_canonical=_normalize_dgt(raw_v),
            )
        )
        covered.append(span)

    for m in _RE_TEAC_RG.finditer(text):
        span = m.span()
        if _is_covered(span):
            continue
        raw_rg = m.group(0)
        citations.append(
            Citation(
                kind="jurisprudence",
                raw=raw_rg,
                span=span,
                juris_kind="TEAC",
                juris_ref=raw_rg,
                juris_canonical=_normalize_teac(raw_rg),
            )
        )
        covered.append(span)

    for m in _RE_TEAC_CANONICAL.finditer(text):
        span = m.span()
        if _is_covered(span):
            continue
        raw_teac = m.group(0)
        citations.append(
            Citation(
                kind="jurisprudence",
                raw=raw_teac,
                span=span,
                juris_kind="TEAC",
                juris_ref=raw_teac,
                juris_canonical=_normalize_teac(raw_teac),
            )
        )
        covered.append(span)

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

    citations = _drop_redundant_family_tokens(citations)
    citations.sort(key=lambda c: c.span[0])
    return citations


def _drop_redundant_family_tokens(citations: list[Citation]) -> list[Citation]:
    """Elimina citas jurisprudenciales redundantes "familia desnuda".

    Un match loose como "TEAC" o "STS" sin `juris_ref` aporta solo ruido
    si junto a él aparece un identificador canónico (ECLI / `V0123-24` /
    `00/12345/2023`) — el LLM está usando la palabra como artículo del
    identificador, no como cita independiente. Sin este filtro, una
    respuesta correcta "El TEAC en 00/12345/2023 unificó criterio"
    generaría una warning `AMBIGUOUS_REFERENCE` espuria por el "TEAC"
    pelado.

    Regla: descartar citas con `juris_canonical=None` y `juris_ref=None`
    si existe otra cita jurisprudencial con `juris_canonical != None`
    cuya separación textual sea <= 80 caracteres.
    """
    canonical_spans = [
        c.span
        for c in citations
        if c.kind == "jurisprudence" and c.juris_canonical is not None
    ]
    if not canonical_spans:
        return citations
    out: list[Citation] = []
    for c in citations:
        if (
            c.kind == "jurisprudence"
            and c.juris_canonical is None
            and c.juris_ref is None
        ):
            redundant = False
            for cs in canonical_spans:
                gap = max(0, max(c.span[0], cs[0]) - min(c.span[1], cs[1]))
                if gap <= 80:
                    redundant = True
                    break
            if redundant:
                continue
        out.append(c)
    return out


def _normalize_dgt(raw: str) -> str:
    """Normaliza `V0123-24` o `V123-2024` a `V0123-24` (padding canónico)."""
    cleaned = raw.strip().upper().replace(" ", "")
    if "-" not in cleaned:
        return cleaned
    prefix, _, anyo = cleaned.partition("-")
    try:
        num = int(prefix[1:])
        anyo_int = int(anyo)
    except ValueError:
        return cleaned
    yy = anyo_int % 100 if anyo_int >= 100 else anyo_int
    return f"V{num:04d}-{yy:02d}"


def _normalize_teac(raw: str) -> str:
    """Normaliza variantes TEAC a `DD/NNNNN/AAAA`. Asume TEAC central (00)
    cuando no viene el código de TEA explícito (forma R.G.)."""
    cleaned = raw.strip().upper()
    cleaned = re.sub(r"^R\.?\s*G\.?[\.:/\s]+", "", cleaned)
    parts = [p.strip() for p in cleaned.split("/") if p.strip()]
    if len(parts) == 2:
        try:
            num = int(parts[0])
            anyo = int(parts[1])
        except ValueError:
            return cleaned
        anyo_full = anyo if anyo >= 100 else 2000 + anyo
        return f"00/{num:05d}/{anyo_full:04d}"
    if len(parts) >= 3:
        try:
            tea = int(parts[0])
            num = int(parts[1])
            anyo = int(parts[2])
        except ValueError:
            return cleaned
        anyo_full = anyo if anyo >= 100 else 2000 + anyo
        base = f"{tea:02d}/{num:05d}/{anyo_full:04d}"
        for s in parts[3:]:
            base += f"/{s}"
        return base
    return cleaned


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
    jurisprudence_registry: JurisprudenceRegistry | None = None,
) -> CitationCheckResult:
    """Verifica todas las citas detectadas en `text`.

    `devengo` se usa para evaluar la vigencia de las normas en esa fecha. Si
    no se proporciona, se evalúa contra `date.today()` con un mensaje
    explícito en el motivo si una norma resulta derogada.

    `extra_documented_sources` añade pinpoints documentados al índice
    de verificación. Útil para tributos modelados en módulos separados
    (`iva`, `is`, …) que no figuran en el `corpus` principal de
    deducciones IRPF pero cuyas citas el LLM puede emitir legítimamente.

    `jurisprudence_registry` activa la verificación dura de citas
    jurisprudenciales/doctrinales. Política aplicada cuando la cita es
    un identificador canónico (ECLI, `V0123-24`, `00/12345/2023`):

    - Identificador canónico EN corpus → `safe`.
    - Identificador canónico NO en corpus → `block` (potencial alucinación).

    Para citas no canónicas (`STS 1234/2020`, `consulta DGT` genérica) →
    `warn` ambiguo: el motor no puede confirmar y el LLM debería citar
    con identificador canónico para ser auditable. Sin registry
    inyectado, todo se queda en `warn` indicando corpus no indexado
    (comportamiento previo).
    """
    citations = _associate_articles_with_normas(extract_citations(text))
    sources_index = _index_sources(corpus, scales, extra_documented_sources)
    devengo_eff = devengo or date.today()
    issues: list[CitationIssue] = []

    for citation in citations:
        if citation.kind == "jurisprudence":
            juris_issue = _verify_jurisprudence(citation, jurisprudence_registry)
            if juris_issue is not None:
                issues.append(juris_issue)
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


def _verify_jurisprudence(
    citation: Citation,
    jurisprudence_registry: JurisprudenceRegistry | None,
) -> CitationIssue | None:
    """Aplica la política de verificación a una cita jurisprudencial.

    Devuelve `None` si la cita es `safe` (identificador canónico presente
    en el corpus). En cualquier otro caso devuelve el `CitationIssue`
    correspondiente.

    La distinción canónico vs ambiguo se basa en `juris_canonical`: las
    citas con ECLI completo o número canónico DGT/TEAC llevan ese campo
    relleno; las formas ambiguas como `STS 1234/2020` lo dejan en None.
    """
    juris_kind = citation.juris_kind or "JURISPRUDENCE"
    juris_ref = citation.juris_ref or ""
    raw_label = f"{juris_kind} {juris_ref}".strip() if juris_ref else juris_kind

    canonical = citation.juris_canonical

    # Sin registry: comportamiento previo — todo `warn` corpus no indexado.
    if jurisprudence_registry is None:
        return CitationIssue(
            level="warning",
            code="JURISPRUDENCE_NOT_INDEXED",
            message=(
                f"Cita a {raw_label} sin corpus de jurisprudencia "
                "indexado: el motor no puede verificar su existencia "
                "ni vigencia."
            ),
            citation=citation,
        )

    # Con registry pero sin identificador canónico: cita ambigua.
    if canonical is None:
        return CitationIssue(
            level="warning",
            code="JURISPRUDENCE_AMBIGUOUS_REFERENCE",
            message=(
                f"Cita a {raw_label} sin identificador canónico (ECLI, "
                "V0123-24 o número TEAC). El motor no puede verificarla "
                "contra el corpus auditable; cita con ECLI/numero canónico "
                "para que sea contrastable."
            ),
            citation=citation,
        )

    # Identificador canónico: cruzar contra registry según familia.
    if juris_kind == "ECLI":
        if jurisprudence_registry.knows_ecli(canonical):
            return None
        return CitationIssue(
            level="blocking",
            code="ECLI_NOT_IN_CORPUS",
            message=(
                f"El ECLI {canonical} no figura en el corpus de "
                "jurisprudencia indexado. Posible cita alucinada — "
                "verifica el identificador en CENDOJ antes de citarlo."
            ),
            citation=citation,
        )

    if juris_kind == "DGT":
        if jurisprudence_registry.knows_dgt(canonical):
            return None
        return CitationIssue(
            level="blocking",
            code="DGT_NOT_IN_CORPUS",
            message=(
                f"La consulta DGT {canonical} no figura en el corpus "
                "indexado. Posible cita alucinada — verifica el número "
                "en el buscador de consultas vinculantes de la AEAT."
            ),
            citation=citation,
        )

    if juris_kind == "TEAC":
        if jurisprudence_registry.knows_teac(canonical):
            return None
        return CitationIssue(
            level="blocking",
            code="TEAC_NOT_IN_CORPUS",
            message=(
                f"La resolución TEAC {canonical} no figura en el corpus "
                "indexado. Posible cita alucinada — verifica la "
                "reclamación en DYCTEA antes de citarla."
            ),
            citation=citation,
        )

    # Familia con canonical pero no DGT/TEAC/ECLI: tratar como ambigua.
    return CitationIssue(
        level="warning",
        code="JURISPRUDENCE_AMBIGUOUS_REFERENCE",
        message=(
            f"Cita a {raw_label} con identificador no soportado por el "
            "verificador. Cita con ECLI para sentencias o número canónico "
            "para DGT/TEAC."
        ),
        citation=citation,
    )
