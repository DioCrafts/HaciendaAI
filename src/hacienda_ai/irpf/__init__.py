"""Cálculo determinista de la cuota IRPF.

El motor de deducciones (`hacienda_ai.rules`) evalúa cada deducción aislada;
este paquete compone el resultado completo del impuesto a partir de las bases
imponibles, el mínimo personal y familiar y las escalas progresivas (estatal
y, cuando esté registrada, autonómica). Las escalas viven en JSON con cita
BOE y se cargan igual que las deducciones.
"""

from .quota import (
    DeductionApplication,
    QuotaResult,
    ScaleApplication,
    apply_progressive_scale,
    compute_quota,
)
from .scales import (
    Bracket,
    TaxScale,
    load_tax_scales,
    select_scale,
)

__all__ = [
    "Bracket",
    "DeductionApplication",
    "QuotaResult",
    "ScaleApplication",
    "TaxScale",
    "apply_progressive_scale",
    "compute_quota",
    "load_tax_scales",
    "select_scale",
]
