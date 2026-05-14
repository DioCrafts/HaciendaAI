# Motor de reglas

El motor vive en `src/hacienda_ai/rules.py`.

## Estados

- `applies`: cumple requisitos y documentación.
- `does_not_apply`: no cumple ejercicio, región o requisitos.
- `missing_data`: faltan hechos fiscales.
- `missing_evidence`: parece aplicable, pero faltan documentos.
- `pending_validation`: regla sin fuente o tests suficientes.

## Tipos de cálculo soportados

- `manual_review`: la regla queda señalada como candidata pero no calcula importe.
- `fixed_amount`: importe fijo desde `fixed_amount`.
- `amount_field`: copia el valor de `base_field` del perfil; aplica `limit` si está fijado.
- `percentage_with_cap`: aplica `percentage` (0-1) sobre `base_field` y respeta `cap` y `limit`.
- `tiered_percentage`: tramos progresivos definidos en `tiers`. Cada tramo tiene `up_to` (umbral acumulado, `null` solo en el último) y `percentage` (0-1). Los umbrales deben ser estrictamente crecientes. Ejemplo (donativos Ley 49/2002): `[{"up_to": 250, "percentage": 0.80}, {"up_to": null, "percentage": 0.40}]`.

## Límites

El motor solo evalúa datos estructurados. No interpreta texto libre ni inventa importes.
