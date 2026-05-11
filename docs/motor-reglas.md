# Motor de reglas

El motor vive en `src/hacienda_ai/rules.py`.

## Estados

- `applies`: cumple requisitos y documentación.
- `does_not_apply`: no cumple ejercicio, región o requisitos.
- `missing_data`: faltan hechos fiscales.
- `missing_evidence`: parece aplicable, pero faltan documentos.
- `pending_validation`: regla sin fuente o tests suficientes.

## Límites

El motor solo evalúa datos estructurados. No interpreta texto libre ni inventa importes.
