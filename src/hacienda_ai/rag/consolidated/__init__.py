"""Detección de cambios legislativos en el texto consolidado del BOE.

El texto **consolidado** de una norma (https://www.boe.es/datosabiertos/
api/legislacion-consolidada) es el agregado vivo: la redacción original
más todas las modificaciones aplicadas hasta la fecha, organizada por
`<bloque>` (artículo, DA, DT, DF, DD…) y, dentro de cada bloque, por
`<version>` con sus fechas de vigencia.

Este módulo construye una huella estable de ese consolidado a una fecha
dada (típicamente HOY) y la compara contra la huella de la última
ejecución. Si el hash de algún bloque cambia, hubo cambio legislativo
real en esa norma; lo reportamos como `ArticleDrift` y disparamos la
invalidación de caches del RAG asociadas a (boe_id, article).

Diseño:
- `fetcher.py`: descarga del XML consolidado con cache local, retry y
  rate-limit. Independiente de `verify_seed.py` para no romper su
  contrato (sus tests cargan el script como módulo).
- `articles.py`: parser del XML consolidado: itera bloques precepto y
  selecciona la versión vigente en una fecha dada. Excluye `nota_pie*`,
  que es metadato editorial del BOE, no normativo.
- `snapshot.py`: modelo `NormaSnapshot` con hashes por bloque + I/O a
  `data/normas/snapshots/<boe_id>.json`.
- `drift.py`: diff entre dos snapshots → lista de artículos
  added/removed/modified.

Política temporal: hasheamos la versión vigente en `reference_date`
(por defecto, hoy). Para normas derogadas el contenido es estático, así
que el caller decide si las salta. Para normas vigentes, comparar el
hash de hoy con el de ayer atrapa cualquier modificación publicada en
el BOE de la víspera.
"""

from __future__ import annotations

from .articles import (
    NON_NORMATIVE_CSS_CLASSES,
    BlockHash,
    all_block_hashes,
    iter_precept_blocks,
    normalize_version_text,
    select_version_for_date,
)
from .drift import (
    ArticleDrift,
    DriftKind,
    NormaDriftReport,
    compute_norma_drift,
)
from .fetcher import (
    ConsolidatedFetcher,
    ConsolidatedFetchError,
)
from .runner import (
    CheckRunReport,
    NormaCheckOutcome,
    check_norma,
    run_check_for_registry,
    serialize_report,
)
from .snapshot import (
    NormaSnapshot,
    SnapshotError,
    load_snapshot,
    save_snapshot,
    snapshot_path,
)

__all__ = [
    "ArticleDrift",
    "BlockHash",
    "CheckRunReport",
    "ConsolidatedFetchError",
    "ConsolidatedFetcher",
    "DriftKind",
    "NON_NORMATIVE_CSS_CLASSES",
    "NormaCheckOutcome",
    "NormaDriftReport",
    "NormaSnapshot",
    "SnapshotError",
    "all_block_hashes",
    "check_norma",
    "compute_norma_drift",
    "iter_precept_blocks",
    "load_snapshot",
    "normalize_version_text",
    "run_check_for_registry",
    "save_snapshot",
    "select_version_for_date",
    "serialize_report",
    "snapshot_path",
]
