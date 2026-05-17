"""Construye el contexto que se entrega al LLM a partir de matches RAG.

Formato canónico (legible para humanos y modelos):

    [FUENTE 1] Norma — Ley 35/2006 IRPF, art. 19, apartado 2.e)
      (BOE-A-2006-20764, vigente desde 2015-01-01)
      Jerarquía: TÍTULO III > CAPÍTULO I > Sección 1ª
      Texto:
      Los gastos de defensa jurídica derivados directamente de litigios...

    [FUENTE 2] Consulta DGT V0123-24 (30/01/2024, IRPF)
      Asunto: Gastos de defensa jurídica en procedimiento tributario
      Criterio: No son deducibles al amparo del art. 19.2.e) LIRPF...

    [FUENTE 3] Sentencia TS — ECLI:ES:TS:2024:1234 (15/06/2024)
      Sala 3ª, Sección 2ª. Fallo: desestimatoria.
      Ratio: Esta Sala considera que los gastos satisfechos en un...

El system prompt del LLM se complementa con la instrucción: "responde
SOLO con información extraída de las FUENTES de arriba; cita usando
[FUENTE N]; si la cuestión no se puede responder con el contexto
provisto, dilo explícitamente y no inventes". El `citation_validator`
luego verifica que se cumple.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..vector import SourceType, VectorMatch


@dataclass(frozen=True)
class ContextSource:
    """Un chunk preparado para entregar al LLM.

    `index` es el ordinal `[FUENTE N]` (1-based) usado en el texto
    formateado y por el `citation_validator` para verificar citas.
    `chunk_id` permite trazabilidad: el orchestrator del chat lo
    persiste en el log de la conversación.
    """

    index: int
    chunk_id: str
    source_type: SourceType
    header: str
    metadata_lines: tuple[str, ...]
    body: str

    def render(self) -> str:
        """Renderiza la fuente como bloque de texto entregable al LLM."""
        parts = [f"[FUENTE {self.index}] {self.header}"]
        parts.extend(f"  {line}" for line in self.metadata_lines)
        parts.append("  Texto:")
        for line in self.body.split("\n"):
            parts.append(f"  {line}")
        return "\n".join(parts)


@dataclass(frozen=True)
class BuiltContext:
    """Resultado de `build_llm_context`."""

    sources: tuple[ContextSource, ...]
    rendered: str = ""
    source_ids_by_index: dict[int, str] = field(default_factory=dict)


def format_metadata_for_llm(
    metadata: dict[str, Any],
) -> tuple[str, ...]:
    """Selecciona y formatea las claves de metadata más útiles para citar.

    El LLM no necesita ver TODA la metadata (puede ser ruidosa); solo
    las claves que aporten valor pinpoint o de vigencia. El orden de
    presentación favorece la cita rápida.
    """
    lines: list[str] = []

    # Identificadores canónicos primero.
    if "boe_id" in metadata:
        lines.append(f"BOE-ID: {metadata['boe_id']}")
    if "ecli" in metadata:
        lines.append(f"ECLI: {metadata['ecli']}")
    if "numero" in metadata:
        lines.append(f"Número: {metadata['numero']}")

    # Pinpoint legal.
    if "articulo" in metadata:
        articulo = metadata["articulo"]
        apartado = metadata.get("apartado")
        if apartado:
            lines.append(f"Pinpoint: {articulo}, apartado {apartado}")
        else:
            lines.append(f"Pinpoint: {articulo}")
    elif metadata.get("apartado"):
        lines.append(f"Pinpoint: apartado {metadata['apartado']}")

    # Vigencia (clave para que el LLM cite a la fecha correcta).
    vd = metadata.get("vigencia_desde") or metadata.get("effective_from")
    vh = metadata.get("vigencia_hasta") or metadata.get("effective_to")
    if vd or vh:
        suffix = "" if vh is None else f" hasta {vh}"
        if vd:
            lines.append(f"Vigencia: desde {vd}{suffix}")
        else:
            lines.append(f"Vigencia: hasta {vh}")

    # Jurisprudencia / doctrina administrativa.
    if "organo" in metadata:
        organo = metadata["organo"]
        tribunal = metadata.get("tribunal_codigo")
        if tribunal:
            lines.append(f"Órgano: {organo.upper()} ({tribunal})")
        else:
            lines.append(f"Órgano: {organo.upper()}")
    if "tipo_resolucion" in metadata:
        lines.append(f"Tipo: {metadata['tipo_resolucion']}")
    if "sentido" in metadata:
        lines.append(f"Sentido: {metadata['sentido']}")
    if "fallo_sentido" in metadata:
        lines.append(f"Fallo: {metadata['fallo_sentido']}")

    # Manuales AEAT / INFORMA.
    if "fuente" in metadata and "boe_id" not in metadata:
        lines.append(f"Fuente AEAT: {metadata['fuente']}")
    if "ejercicio" in metadata:
        lines.append(f"Ejercicio: {metadata['ejercicio']}")
    if "page_inicio" in metadata:
        lines.append(f"Página: {metadata['page_inicio']}")

    # Jerarquía documental (cuando se indexa con legal_chunker).
    jerarquia = metadata.get("jerarquia")
    if jerarquia and isinstance(jerarquia, (list, tuple)) and jerarquia:
        lines.append("Jerarquía: " + " > ".join(str(x) for x in jerarquia))

    # Impuesto siempre al final como tag de búsqueda.
    if "impuesto" in metadata:
        lines.append(f"Impuesto: {str(metadata['impuesto']).upper()}")

    return tuple(lines)


def _source_header(match: VectorMatch) -> str:
    """Construye la cabecera humana de una fuente.

    Resume el tipo + identificador en una línea para que el LLM tenga
    contexto rápido sin tener que parsear toda la metadata.
    """
    meta = match.chunk.metadata
    st = match.chunk.source_type
    if st == SourceType.NORMA:
        bid = meta.get("boe_id", "?")
        articulo = meta.get("articulo")
        apartado = meta.get("apartado")
        pin = f", {articulo}"
        if articulo and apartado:
            pin = f", {articulo}.{apartado}"
        return f"Norma — {bid}{pin}"
    if st == SourceType.SENTENCIA:
        ecli = meta.get("ecli", "?")
        tribunal = meta.get("tribunal_codigo", "?")
        fecha = meta.get("fecha", "?")
        return f"Sentencia {tribunal} — {ecli} ({fecha})"
    if st == SourceType.CONSULTA_DGT:
        numero = meta.get("numero", "?")
        impuesto = str(meta.get("impuesto", "")).upper()
        fecha = meta.get("fecha", "?")
        return f"Consulta DGT {numero} ({fecha}, {impuesto})"
    if st == SourceType.RESOLUCION_TEAC:
        numero = meta.get("numero", "?")
        organo = str(meta.get("organo", "")).upper()
        fecha = meta.get("fecha", "?")
        tipo = meta.get("tipo_resolucion", "")
        if tipo:
            return f"{organo} — {numero} ({fecha}, {tipo})"
        return f"{organo} — {numero} ({fecha})"
    if st == SourceType.MANUAL:
        fuente = meta.get("fuente", "manual")
        ejercicio = meta.get("ejercicio")
        if ejercicio:
            return f"Manual AEAT — {fuente} {ejercicio}"
        return f"Manual AEAT — {fuente}"
    return f"Fuente {st.value}"


def build_llm_context(
    matches: list[VectorMatch],
    *,
    max_sources: int = 12,
) -> BuiltContext:
    """Convierte matches RAG en contexto numerado para el LLM.

    Limita a `max_sources` para no inflar el prompt: el rerank ya ha
    seleccionado los más relevantes, los sobrantes raramente aportan.
    """
    sources: list[ContextSource] = []
    truncated = matches[:max_sources]
    for index, match in enumerate(truncated, start=1):
        sources.append(
            ContextSource(
                index=index,
                chunk_id=match.chunk.chunk_id,
                source_type=match.chunk.source_type,
                header=_source_header(match),
                metadata_lines=format_metadata_for_llm(match.chunk.metadata),
                body=match.chunk.text.strip(),
            )
        )
    rendered_blocks = [s.render() for s in sources]
    rendered = "\n\n".join(rendered_blocks)
    return BuiltContext(
        sources=tuple(sources),
        rendered=rendered,
        source_ids_by_index={s.index: s.chunk_id for s in sources},
    )
