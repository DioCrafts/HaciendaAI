"""Orquestador: XML consolidado BOE → `IndexableChunk` por artículo/apartado.

Combina `hierarchy.iter_precepts_with_context` y
`splitter.split_article_into_apartados` para producir, por cada norma
y versión vigente, una lista de chunks con metadata jerárquica:

    {
      "boe_id": "BOE-A-2006-20764",
      "articulo": "art. 19",
      "apartado": "2.e)",
      "vigencia_desde": "2015-01-01",
      "vigencia_hasta": null,
      "jerarquia": ["TÍTULO III", "CAPÍTULO I", "Sección 1ª"],
      "kind": "ley",
      "effective_from": "2015-01-01",   # alias para filtro temporal RAG.
      "effective_to": null,
    }

`effective_from` y `effective_to` se duplican como campos top-level
para que el filtro temporal del retrieval (memoria/Qdrant) los
encuentre con los nombres convencionales.

`iter_norma_chunks_hierarchical(normas_dir, consolidated_loader)` itera
todo el registro de normas estatales y produce chunks usando el
consolidado de cada una. El `consolidated_loader` se inyecta (cliente
de Qdrant del consolidado o un stub que lee fixtures).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable, Iterator

from ...models import NormaRegistry, NormaStatus
from ..consolidated.articles import (
    normalize_version_text,
    select_version_for_date,
)
from ..vector import IndexableChunk, SourceType
from ..vector.corpus import _merge_norma_files
from .hierarchy import HierarchyContext, iter_precepts_with_context
from .splitter import split_article_into_apartados


class LegalChunkBuildError(RuntimeError):
    """Fallo al construir chunks de una norma."""


def _format_articulo(block_id: str) -> str:
    """Convierte un block_id BOE (`a81bis`, `dadecimoctava`) a etiqueta humana.

    Mantiene una correspondencia simple y verbosa para que la metadata
    sea legible y autoexplicativa en la respuesta del LLM:

    - `a19` → `art. 19`
    - `a81bis` → `art. 81 bis`
    - `a23ter` → `art. 23 ter`
    - `dadecimoctava` → `DA decimoctava`
    - `dtdecimoquinta` → `DT decimoquinta`
    - `df1` → `DF 1`
    - cualquier otro → el `block_id` tal cual con un prefijo
      "preámbulo:" si el caller lo identifica como no-precepto.
    """
    import re

    bid = block_id.lower()
    art_match = re.match(
        r"^a(?P<num>\d+)(?P<suffix>bis|ter|quater|quinquies|sexies)?$", bid
    )
    if art_match:
        num = art_match.group("num")
        suffix = art_match.group("suffix")
        return f"art. {num} {suffix}" if suffix else f"art. {num}"
    if bid.startswith("da"):
        rest = bid[2:]
        return f"DA {rest}" if rest else "DA"
    if bid.startswith("dt"):
        rest = bid[2:]
        return f"DT {rest}" if rest else "DT"
    if bid.startswith("df"):
        rest = bid[2:]
        return f"DF {rest}" if rest else "DF"
    if bid.startswith("dd"):
        rest = bid[2:]
        return f"DD {rest}" if rest else "DD"
    return block_id


def _chunk_id(
    *,
    boe_id: str,
    block_id: str,
    apartado: str | None,
    version_effective_from: date,
) -> str:
    """Id estable. Incluye `vigencia_desde` para distinguir versiones.

    Formato:
        norma::<boe_id>::<block_id>[:apartado]::v<YYYY-MM-DD>

    El apartado se serializa reemplazando caracteres problemáticos
    (`)` → ``, `.` → `_`) para que el id sea ASCII/safe.
    """
    base = f"norma::{boe_id}::{block_id}"
    if apartado is not None:
        sanitized = apartado.replace(")", "").replace(".", "_")
        base += f"::ap{sanitized}"
    base += f"::v{version_effective_from.isoformat()}"
    return base


def build_legal_chunks(
    *,
    xml: str,
    boe_id: str,
    kind: str,
    version_effective_from: date,
    version_effective_to: date | None,
    reference_date: date | None = None,
) -> list[IndexableChunk]:
    """Procesa XML consolidado de una norma y devuelve chunks por (art, apartado).

    `reference_date` (por defecto `version_effective_from`) decide qué
    `<version>` de cada bloque seleccionar — usamos la vigente en esa
    fecha. Si no hay versión que cubra `reference_date`, se omite el
    bloque (caso típico: artículo introducido por reforma posterior).

    El caller pasa la `version_effective_from` y `version_effective_to`
    de la norma para que se peguen como metadata a cada chunk; el
    filtro temporal del retrieval las usa para excluir resultados
    derogados.
    """
    ref_date = reference_date or version_effective_from
    chunks: list[IndexableChunk] = []

    for precept, hierarchy in iter_precepts_with_context(xml):
        version_body = select_version_for_date(precept.raw_body, ref_date)
        if version_body is None:
            continue
        article_text = normalize_version_text(version_body)
        if not article_text.strip():
            continue
        articulo_label = _format_articulo(precept.block_id)

        for apartado in split_article_into_apartados(article_text):
            if not apartado.texto.strip():
                continue
            metadata = _build_metadata(
                boe_id=boe_id,
                kind=kind,
                articulo=articulo_label,
                block_id=precept.block_id,
                apartado=apartado.numero,
                hierarchy=hierarchy,
                effective_from=version_effective_from,
                effective_to=version_effective_to,
            )
            chunks.append(
                IndexableChunk(
                    chunk_id=_chunk_id(
                        boe_id=boe_id,
                        block_id=precept.block_id,
                        apartado=apartado.numero,
                        version_effective_from=version_effective_from,
                    ),
                    source_type=SourceType.NORMA,
                    text=_build_chunk_text(
                        articulo=articulo_label,
                        apartado=apartado.numero,
                        hierarchy=hierarchy,
                        body=apartado.texto,
                    ),
                    metadata=metadata,
                )
            )
    return chunks


def _build_metadata(
    *,
    boe_id: str,
    kind: str,
    articulo: str,
    block_id: str,
    apartado: str | None,
    hierarchy: HierarchyContext,
    effective_from: date,
    effective_to: date | None,
) -> dict[str, object]:
    """Construye la metadata jerárquica del chunk.

    `effective_from`/`effective_to` se duplican como alias en
    `vigencia_desde`/`vigencia_hasta` para que la metadata sea
    autoexplicativa al inspeccionarla manualmente, sin perder la
    clave convencional que usa el filtro temporal del retrieval.
    """
    meta: dict[str, object] = {
        "boe_id": boe_id,
        "kind": kind,
        "articulo": articulo,
        "block_id": block_id,
        "effective_from": effective_from.isoformat(),
        "vigencia_desde": effective_from.isoformat(),
        "jerarquia": list(hierarchy.as_tuple()),
    }
    if apartado is not None:
        meta["apartado"] = apartado
    if effective_to is not None:
        meta["effective_to"] = effective_to.isoformat()
        meta["vigencia_hasta"] = effective_to.isoformat()
    if hierarchy.titulo:
        meta["titulo"] = hierarchy.titulo
    if hierarchy.capitulo:
        meta["capitulo"] = hierarchy.capitulo
    if hierarchy.seccion:
        meta["seccion"] = hierarchy.seccion
    if hierarchy.subseccion:
        meta["subseccion"] = hierarchy.subseccion
    return meta


def _build_chunk_text(
    *,
    articulo: str,
    apartado: str | None,
    hierarchy: HierarchyContext,
    body: str,
) -> str:
    """Construye el texto a embebido: cabecera + cuerpo.

    La cabecera da contexto jerárquico al embedding ("LIRPF, art. 19.2,
    Sección 1ª de Capítulo I del Título III"). Aunque el cuerpo del
    apartado sea breve, la cabecera ayuda al retrieval a desambiguar
    contra artículos de otras normas o secciones distintas con
    contenido similar.
    """
    parts: list[str] = []
    hierarchy_tail = " > ".join(hierarchy.as_tuple())
    if hierarchy_tail:
        parts.append(hierarchy_tail)
    if apartado is not None:
        parts.append(f"{articulo}, apartado {apartado}")
    else:
        parts.append(articulo)
    parts.append(body)
    return "\n\n".join(parts)


# ---------- Iterador agregado ----------


def iter_norma_chunks_hierarchical(
    *,
    normas_dir: Path,
    consolidated_loader: Callable[[str], str | None],
    reference_date: date | None = None,
) -> Iterator[IndexableChunk]:
    """Itera chunks de TODAS las normas estatales del registry.

    Para cada `Norma` estatal con versión vigente:

    1. Pide al `consolidated_loader` el XML consolidado vía `boe_id`.
       Si devuelve `None`, salta la norma (no tenemos consolidado
       cacheado/disponible).
    2. Construye chunks por artículo/apartado con
       `build_legal_chunks`.
    3. Cede los chunks.

    El `consolidated_loader` es inyectable: en producción es
    `ConsolidatedFetcher.fetch`; en tests es un stub que lee fixtures.
    """
    if not normas_dir.exists():
        return
    try:
        registry = NormaRegistry.from_dict(
            _merge_norma_files(normas_dir)
        )
    except Exception as exc:  # noqa: BLE001
        raise LegalChunkBuildError(
            f"no se pudo cargar el registry: {exc}"
        ) from exc

    for boe_id in registry.all_boe_ids():
        # Solo normas estatales (`BOE-A-...`). Las autonómicas (BOCM…)
        # no tienen consolidado en la API del BOE.
        if not boe_id.startswith("BOE-A-"):
            continue
        norma = registry.get_norma(boe_id)
        if norma is None:
            continue

        try:
            xml = consolidated_loader(boe_id)
        except Exception as exc:  # noqa: BLE001
            raise LegalChunkBuildError(
                f"loader falló para {boe_id}: {exc}"
            ) from exc
        if xml is None:
            continue

        for version in registry.versions_for(boe_id):
            if version.status != NormaStatus.VIGENTE:
                # Derogada / inconstitucional: no la indexamos para RAG.
                continue
            yield from build_legal_chunks(
                xml=xml,
                boe_id=boe_id,
                kind=norma.kind.value,
                version_effective_from=version.effective_from,
                version_effective_to=version.effective_to,
                reference_date=reference_date,
            )
