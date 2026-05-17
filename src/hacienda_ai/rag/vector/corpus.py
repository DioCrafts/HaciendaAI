"""Mapeo de modelos de dominio a `IndexableChunk`.

Cada función `iter_<tipo>_chunks(root_dir)` recorre el directorio
correspondiente del corpus, deserializa los JSON y produce
`IndexableChunk` con:

- `chunk_id` único y prefijado por tipo de fuente.
- `text` listo para embeber: para sentencias y resoluciones combinamos
  asunto + criterio/ratio (es lo más denso semánticamente); para
  consultas DGT, cuestión + criterio + contestación recortada; para
  manuales, el contenido del chunk verbatim; para normas, el preámbulo
  de la norma (la versión completa de artículos vendrá en una iteración
  posterior con detección por bloque).
- `metadata` con campos filtrables: `impuesto`, `fecha`,
  `effective_from`/`effective_to`, `organo`, `tribunal_codigo`, etc.

`iter_corpus_chunks(root)` agrupa todas las fuentes en un solo
iterador, útil para el indexador del CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator

from ...models import (
    ConsultaDGT,
    ManualChunk,
    NormaRegistry,
    ResolucionTEAC,
    Sentencia,
)
from ...safety.jurisprudence_registry import (
    DoctrineWeight,
    JurisprudenceTier,
    compute_sentencia_weights,
    compute_teac_weights,
    tier_for_sentencia,
    tier_for_teac,
)
from .embedded_chunk import IndexableChunk, SourceType


class CorpusLoadError(RuntimeError):
    """Error al cargar fragmentos del corpus."""


# ---------- Normas ----------


def iter_norma_chunks(normas_dir: Path) -> Iterator[IndexableChunk]:
    """Itera chunks correspondientes a `Norma`s del registry.

    Por ahora generamos UN chunk por `VersionNorma` (no por artículo)
    con el `title` + `notes` de la norma. Es suficiente para que el
    retrieval localice la norma adecuada; el detalle por artículo
    pertenece a la indexación del consolidado, fuera del alcance de
    esta tarea.
    """
    if not normas_dir.exists():
        return
    try:
        registry = NormaRegistry.from_dict(
            _merge_norma_files(normas_dir)
        )
    except Exception as exc:  # noqa: BLE001
        raise CorpusLoadError(f"no se pudo cargar registry: {exc}") from exc

    for boe_id in registry.all_boe_ids():
        norma = registry.get_norma(boe_id)
        if norma is None:
            continue
        for version in registry.versions_for(boe_id):
            chunk_id = (
                f"norma::{boe_id}::v{version.effective_from.isoformat()}"
            )
            text_parts = [norma.title]
            if version.notes:
                text_parts.append(version.notes)
            metadata: dict[str, Any] = {
                "boe_id": boe_id,
                "kind": norma.kind.value,
                "enacted_at": norma.enacted_at.isoformat(),
                "effective_from": version.effective_from.isoformat(),
                "status": version.status.value,
            }
            if version.effective_to is not None:
                metadata["effective_to"] = version.effective_to.isoformat()
            yield IndexableChunk(
                chunk_id=chunk_id,
                source_type=SourceType.NORMA,
                text="\n\n".join(text_parts),
                metadata=metadata,
            )


def _merge_norma_files(normas_dir: Path) -> dict[str, Any]:
    """Concatena todos los `*.json` del directorio en un único dict.

    Mismo enfoque que `hacienda_ai.normas.load_norma_registry`, pero
    expuesto aquí para no acoplarnos a esa función (puede evolucionar
    independientemente).
    """
    combined_normas: list[Any] = []
    combined_versions: list[Any] = []
    for file_path in sorted(normas_dir.glob("*.json")):
        raw = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            continue
        normas = raw.get("normas", [])
        versions = raw.get("versions", [])
        if isinstance(normas, list):
            combined_normas.extend(normas)
        if isinstance(versions, list):
            combined_versions.extend(versions)
    return {"normas": combined_normas, "versions": combined_versions}


# ---------- Sentencias ----------


def iter_sentencia_chunks(jurisprudencia_dir: Path) -> Iterator[IndexableChunk]:
    """Itera `IndexableChunk` por cada `Sentencia` del corpus.

    El texto para embedding combina título (resumen + asunto) y
    razonamiento decisivo (ratio decidendi + fallo). Indexar el cuerpo
    completo es ruidoso para corpus medianos; si hace falta, se puede
    añadir como chunks secundarios en iteraciones futuras.

    Cada chunk lleva `tier` (jerarquía: TC=1, TS=2, AN=3, TSJ=4, AP=6)
    y `doctrine_weight` (binding/consolidated/isolated) en la metadata
    para que el reranker pueda priorizar fuentes de mayor peso a igualdad
    de relevancia semántica. La doctrina reiterada (CONSOLIDATED) se
    detecta comparando todas las sentencias del corpus en bloque, así
    que la cargamos primero y luego emitimos los chunks.
    """
    sentencias: list[tuple[Path, Sentencia]] = []
    for path in _iter_jsons(jurisprudencia_dir):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sentencias.append((path, Sentencia.from_dict(data)))
        except Exception as exc:  # noqa: BLE001
            raise CorpusLoadError(
                f"no se pudo cargar sentencia {path}: {exc}"
            ) from exc

    weight_by_ecli = compute_sentencia_weights([s for _, s in sentencias])

    for _, sentencia in sentencias:
        text_parts = [
            f"{sentencia.tribunal_codigo} {sentencia.numero_resolucion or ''} ({sentencia.fecha.isoformat()})",
        ]
        if sentencia.resumen:
            text_parts.append(sentencia.resumen)
        if sentencia.ratio_decidendi:
            text_parts.append(f"Ratio: {sentencia.ratio_decidendi}")
        text_parts.append(f"Fallo: {sentencia.fallo_texto}")

        tier = tier_for_sentencia(sentencia)
        weight = weight_by_ecli.get(
            sentencia.ecli, DoctrineWeight.ISOLATED
        )
        metadata: dict[str, Any] = {
            "ecli": sentencia.ecli,
            "organo": sentencia.organo.value,
            "tribunal_codigo": sentencia.tribunal_codigo,
            "fecha": sentencia.fecha.isoformat(),
            "fallo_sentido": sentencia.fallo_sentido.value,
            "ratio_confidence": sentencia.ratio_confidence.value,
            "tier": int(tier),
            "tier_label": tier.name,
            "doctrine_weight": weight.value,
        }
        if sentencia.sala:
            metadata["sala"] = sentencia.sala
        if sentencia.seccion:
            metadata["seccion"] = sentencia.seccion

        yield IndexableChunk(
            chunk_id=f"sentencia::{sentencia.ecli}",
            source_type=SourceType.SENTENCIA,
            text="\n\n".join(text_parts),
            metadata=metadata,
        )


# ---------- Consultas DGT ----------


def iter_dgt_chunks(dgt_dir: Path) -> Iterator[IndexableChunk]:
    """Itera chunks por cada consulta DGT del corpus.

    Las consultas DGT vinculantes son criterio administrativo (art. 89
    LGT). Llevan tier `DGT_VINCULANTE` (=5) en metadata para que el
    reranker las posicione bajo TC/TS/TEAC pero sobre TEAR cuando dos
    fuentes empatan en relevancia.
    """
    for path in _iter_jsons(dgt_dir):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            consulta = ConsultaDGT.from_dict(data)
        except Exception as exc:  # noqa: BLE001
            raise CorpusLoadError(
                f"no se pudo cargar consulta DGT {path}: {exc}"
            ) from exc

        text_parts = [
            f"DGT {consulta.numero} ({consulta.fecha_salida.isoformat()}) — {consulta.asunto}",
            f"Cuestión: {consulta.cuestion_planteada[:1500]}",
        ]
        if consulta.criterio:
            text_parts.append(f"Criterio: {consulta.criterio}")
        else:
            # Fallback: incluir parte de la contestación si no hay
            # criterio extraído.
            text_parts.append(
                f"Contestación: {consulta.contestacion_completa[:1500]}"
            )

        metadata: dict[str, Any] = {
            "numero": consulta.numero,
            "impuesto": consulta.impuesto.value,
            "fecha": consulta.fecha_salida.isoformat(),
            "criterio_confidence": consulta.criterio_confidence.value,
            "tier": int(JurisprudenceTier.DGT_VINCULANTE),
            "tier_label": JurisprudenceTier.DGT_VINCULANTE.name,
            # DGT vinculante: binding solo en el supuesto del consultante
            # (art. 89 LGT). ISOLATED por defecto; el LLM puede matizar.
            "doctrine_weight": DoctrineWeight.ISOLATED.value,
        }
        if consulta.normativa:
            metadata["normativa"] = list(consulta.normativa)

        yield IndexableChunk(
            chunk_id=f"consulta_dgt::{consulta.numero}",
            source_type=SourceType.CONSULTA_DGT,
            text="\n\n".join(text_parts),
            metadata=metadata,
        )


# ---------- Resoluciones TEAC ----------


def iter_teac_chunks(teac_dir: Path) -> Iterator[IndexableChunk]:
    """Itera chunks por cada resolución TEAC/TEAR del corpus.

    El tier diferencia TEAC unifica criterio (=2, equivalente a TS para
    la AEAT) de TEAC extiende efectos (=3), TEAC ordinaria (=4) y TEAR
    (=6). El `doctrine_weight` marca como BINDING las unificaciones de
    criterio y extensiones de efectos por su efecto vinculante legal
    (arts. 242 y 244 LGT).
    """
    resoluciones: list[tuple[Path, ResolucionTEAC]] = []
    for path in _iter_jsons(teac_dir):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            resoluciones.append((path, ResolucionTEAC.from_dict(data)))
        except Exception as exc:  # noqa: BLE001
            raise CorpusLoadError(
                f"no se pudo cargar resolución TEAC {path}: {exc}"
            ) from exc

    weight_by_numero = compute_teac_weights([r for _, r in resoluciones])

    for _, resolucion in resoluciones:
        text_parts = [
            f"{resolucion.organo.value.upper()} {resolucion.numero} "
            f"({resolucion.fecha.isoformat()}, {resolucion.tipo.value}) — {resolucion.asunto}",
        ]
        if resolucion.criterio:
            text_parts.append(f"Criterio: {resolucion.criterio}")

        tier = tier_for_teac(resolucion)
        weight = weight_by_numero.get(
            resolucion.numero, DoctrineWeight.ISOLATED
        )
        metadata: dict[str, Any] = {
            "numero": resolucion.numero,
            "organo": resolucion.organo.value,
            "tipo_resolucion": resolucion.tipo.value,
            "sentido": resolucion.sentido.value,
            "impuesto": resolucion.impuesto.value,
            "fecha": resolucion.fecha.isoformat(),
            "criterio_confidence": resolucion.criterio_confidence.value,
            "tier": int(tier),
            "tier_label": tier.name,
            "doctrine_weight": weight.value,
        }
        if resolucion.sede:
            metadata["sede"] = resolucion.sede
        if resolucion.normativa:
            metadata["normativa"] = list(resolucion.normativa)

        yield IndexableChunk(
            chunk_id=f"resolucion_teac::{resolucion.numero}",
            source_type=SourceType.RESOLUCION_TEAC,
            text="\n\n".join(text_parts),
            metadata=metadata,
        )


# ---------- Manuales / INFORMA ----------


def iter_manual_chunks(manuales_dir: Path) -> Iterator[IndexableChunk]:
    """Itera chunks de manuales AEAT y FAQs INFORMA.

    Los `ManualChunk` ya están listos para indexar: el chunker semántico
    los troceó respetando jerarquía. Aquí solo construimos el
    `IndexableChunk` con el texto del chunk verbatim y la metadata
    derivada.
    """
    for path in _iter_jsons(manuales_dir):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            chunk = ManualChunk.from_dict(data)
        except Exception as exc:  # noqa: BLE001
            raise CorpusLoadError(
                f"no se pudo cargar manual chunk {path}: {exc}"
            ) from exc

        # Texto: titulillo + contenido. El titulillo da contexto
        # jerárquico cuando el chunk es muy denso ("1.1.1. Definición
        # legal" ayuda al modelo a saber qué está mirando).
        text = f"{chunk.titulo}\n\n{chunk.contenido}"

        metadata: dict[str, Any] = {
            "fuente": chunk.fuente.value,
        }
        if chunk.ejercicio is not None:
            metadata["ejercicio"] = chunk.ejercicio
        if chunk.capitulo:
            metadata["capitulo"] = chunk.capitulo
        if chunk.seccion:
            metadata["seccion"] = chunk.seccion
        if chunk.subseccion:
            metadata["subseccion"] = chunk.subseccion
        if chunk.referencias_normativas:
            metadata["normativa"] = list(chunk.referencias_normativas)
        if chunk.page_inicio is not None:
            metadata["page_inicio"] = chunk.page_inicio

        yield IndexableChunk(
            chunk_id=f"manual::{chunk.chunk_id}",
            source_type=SourceType.MANUAL,
            text=text,
            metadata=metadata,
        )


# ---------- Agregador ----------


def iter_corpus_chunks(root_data_dir: Path) -> Iterator[IndexableChunk]:
    """Itera TODO el corpus desde la raíz `data/`.

    Recorre los subdirectorios conocidos: `normas/`, `jurisprudencia/`,
    `dgt_consultas/`, `teac_resoluciones/`, `manuales/`. Si alguno no
    existe (corpus vacío), se salta sin error.
    """
    yield from iter_norma_chunks(root_data_dir / "normas")
    yield from iter_sentencia_chunks(root_data_dir / "jurisprudencia")
    yield from iter_dgt_chunks(root_data_dir / "dgt_consultas")
    yield from iter_teac_chunks(root_data_dir / "teac_resoluciones")
    yield from iter_manual_chunks(root_data_dir / "manuales")


def _iter_jsons(directory: Path) -> Iterator[Path]:
    """Recorre recursivamente todos los `.json` en `directory`."""
    if not directory.exists():
        return
    yield from sorted(directory.rglob("*.json"))
