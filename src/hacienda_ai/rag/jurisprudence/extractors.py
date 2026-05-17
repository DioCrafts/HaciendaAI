"""Extracción heurística de fallo (sentido + texto) y ratio decidendi.

La detección automática de la doctrina jurisprudencial es notoriamente
difícil. Lo que aquí se implementa:

**Fallo** — relativamente fiable porque las sentencias siguen un patrón
formal:

- El bloque FALLO contiene una decisión expresada con verbos canónicos
  ("ESTIMAR", "DESESTIMAR", "ESTIMAR PARCIALMENTE", "CASAR Y ANULAR",
  "DECLARAR LA NULIDAD", "INADMITIR"…). Mapeamos esos verbos a
  `FalloSentido`.
- Si el bloque tiene varias decisiones (multi-recurso), tomamos la
  primera y advertimos en el extracto literal.
- Si nada matchea, devolvemos `DESCONOCIDO` (preferimos no inventar).

**Ratio decidendi** — MUCHO más difícil. La doctrina se reparte por
los fundamentos de derecho y a menudo no se separa de obiter dicta.
Estrategia heurística:

1. Si hay frases-marcador explícitas ("se fija como doctrina", "esta
   Sala considera", "doctrina de esta Sala", "criterio reiterado"),
   extraer el párrafo donde aparecen.
2. Si no, devolver el último FJ antes del fallo — suele contener la
   conclusión decisiva.
3. Si tampoco hay FJ identificable, devolver `None`.

El campo se marca SIEMPRE con `RatioConfidence.AUTO`. Un revisor humano
debe leer la sentencia, ajustar/sustituir el texto y promover a
`MANUAL`. Sin esa promoción, el sistema cita la ratio con un disclaimer
visible ("extracto automático no validado").

Estas heurísticas no sustituyen el juicio de un jurista. Sirven para
arrancar el corpus; la calidad real depende del proceso de revisión
posterior.
"""

from __future__ import annotations

import re

from ...models import FalloSentido

# ---------- Fallo ----------

# Verbos del fallo en orden de evaluación: el primero que matchee gana.
# El orden importa: "estimar parcialmente" antes que "estimar" para no
# clasificar erróneamente una estimatoria parcial como total.
_FALLO_PATTERNS: tuple[tuple[re.Pattern[str], FalloSentido], ...] = (
    (
        re.compile(
            r"\bestimar(?:\s+parcialmente|\s+en\s+parte)\b", re.IGNORECASE
        ),
        FalloSentido.ESTIMATORIA_PARCIAL,
    ),
    (
        re.compile(r"\bha\s+lugar\s+en\s+parte\b", re.IGNORECASE),
        FalloSentido.ESTIMATORIA_PARCIAL,
    ),
    (
        re.compile(r"\bdesestimar\b", re.IGNORECASE),
        FalloSentido.DESESTIMATORIA,
    ),
    (
        re.compile(r"\bno\s+ha(?:ber)?\s+lugar\b", re.IGNORECASE),
        FalloSentido.DESESTIMATORIA,
    ),
    (
        re.compile(r"\bestimar\b", re.IGNORECASE),
        FalloSentido.ESTIMATORIA,
    ),
    (
        re.compile(r"\bha\s+lugar\s+al?\s+recurso\b", re.IGNORECASE),
        FalloSentido.ESTIMATORIA,
    ),
    (
        re.compile(r"\binadmitir|inadmisi[óo]n\b", re.IGNORECASE),
        FalloSentido.INADMISION,
    ),
    (
        re.compile(
            r"\b(?:casar|casamos)\b.*\b(?:y\s+anular|anulando)\b",
            re.IGNORECASE | re.DOTALL,
        ),
        FalloSentido.CASACION,
    ),
    (
        re.compile(r"\bdeclarar(?:\s+la)?\s+nulidad\b", re.IGNORECASE),
        FalloSentido.NULIDAD,
    ),
)


def extract_fallo(plain_text: str, fallo_section: str | None) -> tuple[FalloSentido, str]:
    """Devuelve `(sentido_normalizado, texto_literal_extraido)`.

    `fallo_section` es preferido (más limpio). Si es `None` o vacío,
    cae al `plain_text` completo y busca el FALLO heurísticamente.
    """
    source = fallo_section or _heuristic_fallo_chunk(plain_text)
    if not source:
        return FalloSentido.DESCONOCIDO, ""

    # Limita a las primeras 1500 chars del fallo: el verbo está siempre
    # cerca del inicio, y limitar evita capturar texto del razonamiento
    # posterior si la sección está mal segmentada.
    snippet = source.strip()[:1500]

    for pattern, sentido in _FALLO_PATTERNS:
        if pattern.search(snippet):
            return sentido, snippet
    return FalloSentido.DESCONOCIDO, snippet


def _heuristic_fallo_chunk(plain_text: str) -> str | None:
    """Busca el bloque FALLO sin haber segmentado por secciones.

    Útil cuando el HTML no expone el encabezado canónico pero el texto
    sí incluye "F A L L O" o "FALLAMOS" al final.
    """
    candidates = (
        re.search(
            r"(F\s*A\s*L\s*L\s*O\b.+?)(?:\Z)",
            plain_text,
            re.IGNORECASE | re.DOTALL,
        ),
        re.search(
            r"(FALLAMOS\b.+?)(?:\Z)", plain_text, re.IGNORECASE | re.DOTALL
        ),
        re.search(
            r"(PARTE\s+DISPOSITIVA\b.+?)(?:\Z)",
            plain_text,
            re.IGNORECASE | re.DOTALL,
        ),
    )
    for match in candidates:
        if match is not None:
            return match.group(1)
    return None


# ---------- Ratio decidendi ----------

# Frases-marcador que el TS suele usar al fijar doctrina.
_MARCADORES_DOCTRINA = (
    re.compile(r"\bse\s+fija\s+como\s+doctrina\b", re.IGNORECASE),
    re.compile(r"\bdoctrina\s+(?:de\s+esta\s+Sala|jurisprudencial)\b", re.IGNORECASE),
    re.compile(r"\besta\s+Sala\s+(?:considera|entiende|declara)\b", re.IGNORECASE),
    re.compile(r"\bcriterio\s+(?:reiterado|jurisprudencial|de\s+esta\s+Sala)\b", re.IGNORECASE),
    re.compile(r"\bdebe\s+responderse\s+(?:que|a\s+la\s+cuesti[óo]n)\b", re.IGNORECASE),
    re.compile(r"\bcabe\s+concluir\b", re.IGNORECASE),
)

_RE_FJ_HEADING = re.compile(
    r"^\s*(?:FJ\.?\s*\d+|FUNDAMENTO\s+JUR[ÍI]DICO\s+\w+|"
    r"PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|SEXTO|S[ÉE]PTIMO|OCTAVO|"
    r"NOVENO|D[ÉE]CIMO|UND[ÉE]CIMO|DUOD[ÉE]CIMO)"
    r"[\.:\-\s—]",
    re.MULTILINE | re.IGNORECASE,
)


def _split_paragraphs(text: str) -> list[str]:
    """Divide texto en párrafos por líneas en blanco. Ignora vacíos."""
    return [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]


def _max_chars(text: str, limit: int = 1200) -> str:
    """Trunca un extracto a `limit` chars sin partir palabras a medias."""
    if len(text) <= limit:
        return text
    truncated = text[:limit]
    cut = truncated.rfind(" ")
    return (truncated[:cut] if cut > 0 else truncated) + " […]"


def extract_ratio_decidendi(
    plain_text: str,
    *,
    fundamentos_section: str | None = None,
) -> str | None:
    """Extrae el fragmento que probablemente expresa la doctrina decisiva.

    Estrategia:
    1. Buscar frases-marcador explícitas en la sección de fundamentos
       (o en todo el texto si no está segmentada). Si hay una, devolver
       el párrafo entero que la contiene.
    2. Si no, devolver el último párrafo no trivial del último FJ antes
       del fallo.
    3. Si nada de eso es identificable, devolver `None`.

    El extracto se trunca a 1200 chars para que sea citable sin volcar
    folios enteros. El campo SIEMPRE se almacena con
    `RatioConfidence.AUTO`; un humano debe validarlo antes de promover.
    """
    source = fundamentos_section or plain_text
    if not source or not source.strip():
        return None

    paragraphs = _split_paragraphs(source)
    if not paragraphs:
        return None

    # 1) Buscar marcadores explícitos.
    for marcador in _MARCADORES_DOCTRINA:
        for para in paragraphs:
            if marcador.search(para):
                return _max_chars(para)

    # 2) Último FJ antes del fallo: identificamos posiciones de FJ y
    # tomamos el bloque entre el último encabezado FJ y el final del
    # source (que en `fundamentos_section` ya excluye el FALLO).
    fj_matches = list(_RE_FJ_HEADING.finditer(source))
    if fj_matches:
        last_fj_start = fj_matches[-1].start()
        last_fj = source[last_fj_start:].strip()
        # Devolvemos el último párrafo dentro del FJ: suele ser la
        # conclusión decisiva tras el razonamiento.
        ultimos_parrafos = _split_paragraphs(last_fj)
        if ultimos_parrafos:
            # Saltamos el encabezado ("PRIMERO." etc.) si quedó aislado.
            candidate = ultimos_parrafos[-1]
            if len(candidate) < 50 and len(ultimos_parrafos) >= 2:
                candidate = ultimos_parrafos[-2]
            return _max_chars(candidate)

    # 3) Último recurso: devolvemos el último párrafo no trivial.
    last = paragraphs[-1] if len(paragraphs[-1]) > 50 else None
    return _max_chars(last) if last else None
