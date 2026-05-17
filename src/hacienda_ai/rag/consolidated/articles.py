"""Extracción de bloques precepto y hashing por artículo del consolidado BOE.

El XML consolidado del BOE tiene esta forma:

    <legislacion-consolidada>
      ...
      <texto>
        <bloque id="a1" tipo="precepto">
          <version fecha_vigencia="20070101" fecha_vigencia_fin="20141231">
            <p class="parrafo">…</p>
            <p class="nota_pie">…</p>          ← metadato editorial, no normativo
          </version>
          <version fecha_vigencia="20150101">
            <p class="parrafo">…</p>
          </version>
        </bloque>
        <bloque id="a2" tipo="precepto">…</bloque>
        ...
      </texto>
    </legislacion-consolidada>

Conceptos:
- `bloque`: unidad normativa estable (artículo, DA, DT, DF, DD, prefacio).
  Tiene `id` ("a1", "a81bis", "dadecimoctava"…) y `tipo="precepto"` para
  los preceptos. BOE también usa otros `tipo` (estructura, transitoria…)
  que aquí descartamos.
- `version`: redacción concreta del bloque vigente en un intervalo. Si la
  norma se modifica, el BOE añade una `version` nueva con `fecha_vigencia`
  posterior y cierra la anterior con `fecha_vigencia_fin`.
- `<p class="nota_pie*">`: notas al pie editoriales que BOE inserta para
  documentar el histórico de modificaciones del bloque. NO son texto
  normativo y cambian aunque la norma no cambie — excluirlas del hash es
  obligatorio o todos los hashes harían drift falso.

API expuesta:
- `iter_precept_blocks(xml)`: genera `(block_id, body)` por cada bloque
  precepto del XML.
- `select_version_for_date(body, target)`: devuelve el cuerpo de la
  `<version>` vigente en `target`, o `None` si ninguna versión cubre esa
  fecha.
- `normalize_version_text(body)`: aplana a texto plano normativo (sin
  notas al pie ni tags internos) listo para hashear.
- `all_block_hashes(xml, target)`: hashea todos los bloques precepto del
  XML en su versión vigente a `target`. Devuelve `dict[block_id, hash]`.

Coincide en estrategia con `scripts/verify_seed.py`, pero ese script
hashea solo bloques citados por una deducción. Aquí hasheamos todos los
bloques de la norma para detectar cambios fuera del corpus auditable.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from typing import Iterator

from ...models import VersionArticulo

# Clase CSS de párrafos que NO son texto normativo (notas al pie del
# editor BOE: histórico de modificaciones del artículo). Excluirlas del
# hash es obligatorio: el BOE las modifica cada vez que añade una
# referencia editorial sin que la norma haya cambiado.
NON_NORMATIVE_CSS_CLASSES = re.compile(r"^nota_pie(_\d+)?$")

# Capturamos cada `<bloque id="..." tipo="precepto">` con su cuerpo. El
# patrón es non-greedy para no engullir el siguiente bloque.
_RE_PRECEPT_BLOCK = re.compile(
    r'<bloque\s+id="([^"]+)"\s+tipo="precepto"[^>]*>(.*?)</bloque>',
    re.DOTALL,
)

_RE_VERSION = re.compile(r"<version\s+([^>]*)>(.*?)</version>", re.DOTALL)
_RE_FECHA_VIGENCIA = re.compile(r'fecha_vigencia="(\d{8})"')
_RE_FECHA_VIGENCIA_FIN = re.compile(r'fecha_vigencia_fin="(\d{8})"')
_RE_PARRAFO = re.compile(r'<p\s+class="([^"]+)"[^>]*>(.*?)</p>', re.DOTALL)
_RE_INNER_TAGS = re.compile(r"<[^>]+>")
_RE_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class BlockHash:
    """Hash SHA-256 del texto plano de un bloque en su versión vigente."""

    block_id: str
    digest: str
    has_active_version: bool


def iter_precept_blocks(xml: str) -> Iterator[tuple[str, str]]:
    """Itera `(block_id, body)` por cada `<bloque tipo='precepto'>` del XML.

    El orden es el de aparición en el XML, que coincide con el orden
    normativo (preámbulo, articulado, disposiciones). El caller no debe
    asumir un orden específico para diff: el snapshot indexa por
    `block_id`.
    """
    for match in _RE_PRECEPT_BLOCK.finditer(xml):
        yield match.group(1), match.group(2)


def select_version_for_date(body: str, target: date) -> str | None:
    """Devuelve el cuerpo de la `<version>` cuyo intervalo cubre `target`.

    A diferencia de `scripts/verify_seed.py:select_version`, no usamos
    fallback "última versión": si ninguna `<version>` cubre la fecha
    devolvemos `None` para que el caller distinga "bloque sin redacción
    vigente en esa fecha" (puede pasar para artículos futuros o
    derogados) de "bloque vigente con redacción X".
    """
    target_str = target.strftime("%Y%m%d")
    chosen: str | None = None
    chosen_from = ""
    for match in _RE_VERSION.finditer(body):
        attrs, content = match.group(1), match.group(2)
        f_match = _RE_FECHA_VIGENCIA.search(attrs)
        if not f_match:
            continue
        fecha_from = f_match.group(1)
        if fecha_from > target_str:
            continue
        fin_match = _RE_FECHA_VIGENCIA_FIN.search(attrs)
        if fin_match and fin_match.group(1) < target_str:
            continue
        # Empate: gana la `fecha_vigencia` más reciente.
        if chosen is None or fecha_from > chosen_from:
            chosen = content
            chosen_from = fecha_from
    return chosen


def normalize_version_text(version_body: str) -> str:
    """Aplana el cuerpo de una `<version>` a texto plano normativo.

    Concatena todos los `<p>` salvo los de clase `nota_pie*`. Dentro de
    cada `<p>` elimina tags anidados (a, b, sup, …) y colapsa espacios.
    El resultado es estable frente a reordenaciones de atributos XML y
    variaciones de whitespace del editor.
    """
    out: list[str] = []
    for match in _RE_PARRAFO.finditer(version_body):
        cls = match.group(1)
        if NON_NORMATIVE_CSS_CLASSES.match(cls):
            continue
        inner = _RE_INNER_TAGS.sub("", match.group(2))
        inner = _RE_WHITESPACE.sub(" ", inner).strip()
        if inner:
            out.append(inner)
    return "\n".join(out)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_block_at(xml: str, block_id: str, target: date) -> BlockHash | None:
    """Hashea un bloque concreto en su versión vigente a `target`.

    Devuelve `None` si el bloque no existe en el XML; devuelve un
    `BlockHash` con `has_active_version=False` y digest del texto vacío
    si el bloque existe pero ninguna `<version>` cubre la fecha (caso
    raro: bloque derogado antes de `target`).
    """
    for current_id, body in iter_precept_blocks(xml):
        if current_id != block_id:
            continue
        version_body = select_version_for_date(body, target)
        if version_body is None:
            return BlockHash(
                block_id=block_id, digest=_sha256(""), has_active_version=False
            )
        text = normalize_version_text(version_body)
        return BlockHash(
            block_id=block_id, digest=_sha256(text), has_active_version=True
        )
    return None


def all_block_hashes(xml: str, target: date) -> dict[str, str]:
    """Hashea todos los bloques precepto del XML en su versión vigente.

    Bloques sin versión vigente en `target` se omiten del resultado (no
    aparecen en el snapshot). Esto evita que un bloque derogado o pendiente
    de entrar en vigor genere un hash "vacío" que confunda al diff.

    El caller (`compute_norma_drift`) interpreta "ausente" como "no
    aplicable en esta fecha", no como "removed" — `removed` se reserva
    para bloques que SÍ estaban en el snapshot previo y ya no aparecen.
    """
    hashes: dict[str, str] = {}
    for block_id, body in iter_precept_blocks(xml):
        version_body = select_version_for_date(body, target)
        if version_body is None:
            continue
        text = normalize_version_text(version_body)
        hashes[block_id] = _sha256(text)
    return hashes


# ---------- Iteración del timeline completo por artículo ----------
#
# A diferencia de `all_block_hashes(xml, target)` —que selecciona LA
# versión vigente en una fecha—, `iter_article_versions` produce TODAS
# las versiones de cada bloque precepto. Es la operación necesaria para
# construir un `ArticleRegistry` con timeline completo y poder responder
# preguntas como "¿qué texto exacto tenía art. 23 LIRPF en 2018?".
#
# Para `modified_by_boe_id` usamos las `<p class="nota_pie*">` de la
# `<version>`: BOE inserta ahí referencias editoriales del tipo
# "Apartado modificado por el art. 22 de la Ley XX/YYYY, de DD de mes".
# La extracción es best-effort por regex: cuando no se encuentra
# `BOE-A-YYYY-NNNN` o un patrón resoluble en la nota, dejamos
# `modified_by_boe_id=None` (es honesto: el verificador downstream
# no puede inferir lo que no está en el texto).


# `<version>` con TODOS los atributos extraibles. La forma `_RE_VERSION`
# (no-greedy) ya está definida arriba; aquí extraemos también las notas
# al pie para resolver `modified_by`.
_RE_VERSION_NOTA_PIE = re.compile(
    r'<p\s+class="nota_pie(?:_\d+)?"[^>]*>(.*?)</p>',
    re.DOTALL,
)

# Patrones para resolver la norma que introdujo la versión a partir de
# las notas editoriales BOE. Probamos en orden de especificidad:
# 1) referencia BOE-A directa (la forma canónica que el editor BOE usa
#    en consolidados modernos).
# 2) "Ley N/AAAA" cuando no aparece BOE-A pero sí la cita corta.
_RE_NOTA_BOE_REFERENCE = re.compile(r"\bBOE-A-(\d{4})-(\d{1,7})\b")
_RE_NOTA_LAW_REFERENCE = re.compile(
    r"\b(?:Ley|Real\s+Decreto-?[Ll]ey|Real\s+Decreto|RDL|RDLeg|RD)\s+"
    r"(\d+)\s*/\s*(\d{4})\b",
    re.IGNORECASE,
)


def _parse_yyyymmdd(value: str) -> date:
    """`'20180101'` → `date(2018, 1, 1)`. Sin tolerancia: el formato es
    fijo en el XML BOE consolidado."""
    return date(int(value[:4]), int(value[4:6]), int(value[6:8]))


def _extract_modified_by(version_body: str) -> str | None:
    """Extrae `modified_by_boe_id` de las notas al pie de una `<version>`.

    Recorre todas las `<p class="nota_pie*">` del cuerpo y devuelve la
    primera referencia `BOE-A-YYYY-NNNN` que encuentre. Si no hay
    referencia BOE-A directa, busca `Ley N/AAAA` pero NO la resuelve a
    BOE-A automáticamente (sería frágil): devuelve la etiqueta verbatim
    de la cita corta como fallback, prefijada por `"law:"`. El caller
    decide si invocar el resolver del alias o aceptar la etiqueta.
    """
    for note_match in _RE_VERSION_NOTA_PIE.finditer(version_body):
        note_text = note_match.group(1)
        boe_match = _RE_NOTA_BOE_REFERENCE.search(note_text)
        if boe_match:
            return f"BOE-A-{boe_match.group(1)}-{boe_match.group(2)}"
    # Segunda pasada: si no encontramos BOE-A pero hay "Ley N/AAAA".
    for note_match in _RE_VERSION_NOTA_PIE.finditer(version_body):
        law_match = _RE_NOTA_LAW_REFERENCE.search(note_match.group(1))
        if law_match:
            return (
                f"law:{law_match.group(0).strip()}"
            )
    return None


@dataclass(frozen=True)
class _RawArticleVersion:
    """Estructura intermedia del parser, antes de adornarla con metadatos.

    Mantener un struct sin el `norma_boe_id` exterior facilita testing
    del extractor por pasos sin tener que pasar la norma por todas
    partes."""

    article_id: str
    effective_from: date
    effective_to: date | None
    text: str
    text_hash: str
    modified_by_boe_id: str | None


def _iter_raw_article_versions(xml: str) -> Iterator[_RawArticleVersion]:
    """Itera todas las versiones de todos los bloques precepto del XML.

    Salta versiones sin `fecha_vigencia` parseable (raro pero posible si
    el XML está corrupto). El orden de emisión es:
    `(orden de aparición del bloque) × (orden de aparición de versions
    dentro del bloque)`.
    """
    for block_id, body in iter_precept_blocks(xml):
        for version_match in _RE_VERSION.finditer(body):
            attrs, content = version_match.group(1), version_match.group(2)
            f_match = _RE_FECHA_VIGENCIA.search(attrs)
            if not f_match:
                # Sin fecha_vigencia no podemos versionar — skipear.
                continue
            try:
                effective_from = _parse_yyyymmdd(f_match.group(1))
            except (ValueError, IndexError):
                continue
            fin_match = _RE_FECHA_VIGENCIA_FIN.search(attrs)
            effective_to: date | None = None
            if fin_match:
                try:
                    effective_to = _parse_yyyymmdd(fin_match.group(1))
                except (ValueError, IndexError):
                    effective_to = None
            text = normalize_version_text(content)
            if not text:
                # Versión sin texto normativo (puede pasar en bloques
                # solo cabecera): la saltamos. Un timeline con texto
                # vacío rompería las invariantes de VersionArticulo.
                continue
            yield _RawArticleVersion(
                article_id=block_id,
                effective_from=effective_from,
                effective_to=effective_to,
                text=text,
                text_hash=_sha256(text),
                modified_by_boe_id=_extract_modified_by(content),
            )


def iter_article_versions(
    xml: str, *, norma_boe_id: str
) -> Iterator[VersionArticulo]:
    """Itera `VersionArticulo` por cada `<version>` de cada bloque
    precepto del XML consolidado.

    `norma_boe_id` se inyecta porque el XML del consolidado BOE no lo
    lleva en cada bloque (la identidad va en la cabecera del documento).
    El caller (`runner.py`) lo conoce porque ya descargó el documento
    por ese id.

    Esta función es el corazón del parser per-artículo: produce el
    timeline completo que puebla `ArticleRegistry` y, en producción, se
    persiste en `data/normas/article_versions/<boe_id>.json`.
    """
    for raw in _iter_raw_article_versions(xml):
        yield VersionArticulo(
            norma_boe_id=norma_boe_id,
            article_id=raw.article_id,
            effective_from=raw.effective_from,
            effective_to=raw.effective_to,
            text=raw.text,
            text_hash=raw.text_hash,
            modified_by_boe_id=raw.modified_by_boe_id,
        )
