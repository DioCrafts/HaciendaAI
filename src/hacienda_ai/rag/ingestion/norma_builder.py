"""Construcción de `Norma` y `VersionNorma` a partir del sumario y el documento.

Cierra el lazo entre el sumario diario (qué se publicó) y el modelo de
dominio del proyecto (`Norma`/`VersionNorma`). Las decisiones que toma este
módulo son aproximaciones automáticas con buen criterio fiscal por defecto,
**pero sujetas a revisión humana** antes de mergear el PR generado por el
cron — por eso el GitHub workflow abre PR, no push directo.

Decisiones automáticas que el revisor humano puede ajustar:

- `enacted_at`: se intenta extraer del propio título ("Ley X/2024, de N de
  mes, …"). Si la regex falla, se usa la fecha de publicación como
  aproximación.

- `effective_from`: por defecto, día siguiente a la publicación. Es la
  regla supletoria del art. 2.1 del Código Civil ("entrarán en vigor a los
  veinte días de su completa publicación en el Boletín Oficial del Estado,
  si en ellas no se dispone otra cosa"). En la práctica la mayoría de
  normas fiscales fijan entrada en vigor distinta en su DF — esto se
  documenta en `notes` para que el revisor lo verifique.

- `status`: siempre `VIGENTE` al crearla. Cualquier cambio posterior
  (derogación, suspensión, inconstitucionalidad) se introduce manualmente
  modificando este JSON.

- `effective_to`: siempre `None` al crearla. El siguiente legislador que
  modifique la norma generará otra entrada con su propio `effective_from`,
  y un revisor humano cerrará la anterior con `effective_to` y el
  `modified_by_boe_id` correspondiente.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

from ...models import Norma, NormaStatus, SourceKind, VersionNorma
from .boe_summary import SummaryItem
from .tax_filter import Classification


@dataclass(frozen=True)
class BuiltNorma:
    """Par `(Norma, VersionNorma)` listo para persistir en `data/normas/`.

    Conserva además la clasificación que llevó a su creación, útil para
    auditoría y para construir el body del PR.
    """

    norma: Norma
    version: VersionNorma
    classification: Classification
    source_item: SummaryItem


# Meses en castellano usados en los títulos del BOE. Mantenemos los nombres
# con tilde porque BOE los emite así; el lookup se hace tras normalizar
# acentos.
_MESES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

# Captura "de DD de MES" en el título. BOE es muy consistente con este
# formato ("Ley 5/2024, de 28 de enero, …", "Real Decreto 92/2024, de 29
# de enero, …"). El AÑO no aparece tras el mes en el título: viene en el
# número de la norma ("Ley 5/2024"). Las dos piezas se combinan abajo.
_RE_DIA_MES = re.compile(
    r"\bde\s+(\d{1,2})\s+de\s+([a-zñáéíóú]+)\b",
    re.IGNORECASE,
)

# Captura el número de la norma: "Ley 5/2024", "Real Decreto 92/2024",
# "Orden HFP/115/2024", "Real Decreto-ley 8/2023". El año es el último
# grupo \d{4} antes de la primera coma.
_RE_NUMERO_NORMA = re.compile(r"\b(\d{1,4})/(\d{4})\b")


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn").lower()


def parse_enacted_at_from_title(titulo: str, *, fallback: date) -> date:
    """Extrae fecha de promulgación del título BOE.

    BOE no incluye el año junto al día/mes en el título; lo lleva en el
    número de la norma (`Ley X/YYYY`). Combinamos las dos piezas:
    - Día y mes: primer match de `de DD de MES` en el título.
    - Año: primer `X/YYYY` que aparezca en el título.

    Si falta cualquiera de las dos piezas, o si el mes es inválido, o si
    la fecha resultante es inválida (29 de febrero en año no bisiesto),
    devuelve `fallback`. No lanza nunca: la fecha de promulgación es
    información auxiliar, no un campo bloqueante.
    """
    # Preprocesado: "1.º" / "1.°" / "1º" → "1" para que la regex case.
    preprocessed = re.sub(r"(\d+)\s*\.?\s*[ºo°]", r"\1", titulo)

    fecha_match = _RE_DIA_MES.search(preprocessed)
    if fecha_match is None:
        return fallback
    day_raw, month_raw = fecha_match.groups()
    month_key = _strip_accents(month_raw)
    if month_key not in _MESES:
        return fallback

    year_match = _RE_NUMERO_NORMA.search(preprocessed)
    year = int(year_match.group(2)) if year_match else fallback.year

    try:
        return date(year, _MESES[month_key], int(day_raw))
    except ValueError:
        return fallback


def _build_notes(item: SummaryItem, classification: Classification) -> str:
    """Notas para el revisor del PR.

    Incluye keywords detectadas, departamento, y aviso explícito de que
    `effective_from` es una aproximación. El revisor consulta la DF de la
    norma y ajusta si procede.
    """
    parts = [
        f"Ingestada automáticamente del sumario BOE de {item.fecha_publicacion.isoformat()}.",
        f"Departamento: {item.departamento}.",
        f"Epígrafe: {item.epigrafe}.",
        f"Relevancia fiscal: {classification.relevance}.",
    ]
    if classification.matched_keywords:
        parts.append(
            "Keywords detectadas: " + ", ".join(classification.matched_keywords) + "."
        )
    parts.append(
        "ATENCIÓN: `effective_from` es aproximación (día siguiente a publicación, "
        "art. 2.1 CC). Verificar la disposición final de la norma para fijar la "
        "entrada en vigor real antes de mergear."
    )
    return " ".join(parts)


def build_norma(
    item: SummaryItem,
    *,
    classification: Classification,
    content_hash: str,
) -> BuiltNorma:
    """Construye `Norma` + `VersionNorma` a partir de un item clasificado.

    `classification.kind` debe estar resuelto (no `None`): el caller
    descarta items con `kind=None` antes de llegar aquí.
    """
    if classification.kind is None:
        raise ValueError(
            f"build_norma: classification.kind es None para {item.boe_id}"
        )
    enacted_at = parse_enacted_at_from_title(
        item.titulo, fallback=item.fecha_publicacion
    )
    effective_from = item.fecha_publicacion + timedelta(days=1)
    norma = Norma(
        boe_id=item.boe_id,
        kind=classification.kind,
        title=item.titulo,
        enacted_at=enacted_at,
    )
    version = VersionNorma(
        norma_boe_id=item.boe_id,
        effective_from=effective_from,
        effective_to=None,
        status=NormaStatus.VIGENTE,
        content_hash=content_hash,
        modified_by_boe_id=None,
        notes=_build_notes(item, classification),
    )
    return BuiltNorma(
        norma=norma,
        version=version,
        classification=classification,
        source_item=item,
    )


def kind_to_label(kind: SourceKind) -> str:
    """Etiqueta humana del `SourceKind` para logs y body del PR."""
    return {
        SourceKind.LEY_ORGANICA: "Ley Orgánica",
        SourceKind.LEY: "Ley",
        SourceKind.REAL_DECRETO_LEGISLATIVO: "Real Decreto Legislativo",
        SourceKind.REAL_DECRETO_LEY: "Real Decreto-ley",
        SourceKind.REAL_DECRETO: "Real Decreto",
        SourceKind.ORDEN_MINISTERIAL: "Orden Ministerial",
        SourceKind.RESOLUCION: "Resolución",
        SourceKind.DGT_VINCULANTE: "DGT vinculante",
        SourceKind.TEAC: "TEAC",
        SourceKind.TS: "Tribunal Supremo",
        SourceKind.AN: "Audiencia Nacional",
        SourceKind.TSJ: "TSJ",
        SourceKind.MANUAL_AEAT: "Manual AEAT",
        SourceKind.INSTRUCCION_AEAT: "Instrucción AEAT",
        SourceKind.PENDIENTE_VALIDACION: "Pendiente de validación",
    }.get(kind, kind.value)
