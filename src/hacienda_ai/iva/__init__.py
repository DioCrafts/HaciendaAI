"""IVA (Impuesto sobre el Valor Añadido) — modelo, catálogo, cálculo.

Cobertura intencionalmente acotada al **tipo impositivo aplicable** y al
cálculo de la cuota (`base × tipo`). NO se modelan:

- Reglas de localización (LIVA arts. 68-70): requieren conocer país
  del proveedor/cliente y naturaleza del servicio.
- Inversión del sujeto pasivo (art. 84.2): excepciones específicas.
- Prorrata (arts. 102-105): requiere mezcla de operaciones con/sin
  derecho a deducción.
- Regímenes especiales (REAGyP, recargo de equivalencia, agencias de
  viajes, criterio de caja, etc.): cada uno es un capítulo aparte.
- Modificaciones de la base imponible (art. 80) y devoluciones.

Para esos temas, el LLM debe contestar pidiendo más datos al usuario o
remitiendo a un asesor; nunca debe inferir reglas de localización o
prorrata sin más contexto que un descriptor de operación.

Lo que SÍ ofrecemos:

- `IVATipo`: enum con los cuatro tipos legales (general, reducido,
  superreducido, exento) más `cero` para 0% gravado (exportaciones,
  asimiladas).
- `compute_iva_quota(base, tipo)`: cuota IVA con cita pinpoint.
- `lookup_iva_operations(query)`: búsqueda léxica en un catálogo de
  ~25 operaciones típicas — el LLM la usa para identificar el tipo
  aplicable a una operación descrita por el usuario.
- `iva_documented_sources()`: todas las `Source` que se citan en este
  módulo, listas para pasar al `citation_guard` como pinpoints
  documentados (sin esto, el guard marcaría las citas IVA como
  WARN/BLOCK por no figurar en el corpus auditable).
"""

from __future__ import annotations

from .operations import (
    CATALOG,
    IVAOperation,
    iva_documented_sources,
    lookup_iva_operations,
)
from .tipos import (
    IVA_RATES,
    IVA_SOURCES,
    IVAComputationError,
    IVAQuota,
    IVATipo,
    compute_iva_quota,
)

__all__ = [
    "CATALOG",
    "IVAComputationError",
    "IVAOperation",
    "IVAQuota",
    "IVATipo",
    "IVA_RATES",
    "IVA_SOURCES",
    "compute_iva_quota",
    "iva_documented_sources",
    "lookup_iva_operations",
]
