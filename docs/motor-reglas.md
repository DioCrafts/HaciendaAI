# Motor de reglas

El motor vive en `src/hacienda_ai/rules.py`.

## Estados

- `applies`: cumple requisitos y documentación.
- `does_not_apply`: no cumple vigencia, ejercicio, región o requisitos, o la
  norma citada estaba derogada o declarada inconstitucional en el devengo.
- `missing_data`: faltan hechos fiscales.
- `missing_evidence`: parece aplicable, pero faltan documentos.
- `pending_validation`: regla sin fuente o tests suficientes, norma
  suspendida en el devengo, o sin versión registrada en esa fecha.

## Filtro temporal

Antes de evaluar requisitos, el motor comprueba que la fecha del devengo
(31-dic del ejercicio por defecto, o `TaxProfile.devengo_date` si se
indica) cae dentro del intervalo `[effective_from, effective_to]` de la
deducción. Una deducción no vigente en esa fecha se descarta con
`does_not_apply` y `confidence=0.95` aunque el resto de requisitos
encajen.

Si se pasa un `NormaRegistry` opcional a `evaluate_deduction`, el motor
consulta también el estado (`vigente`, `derogada`, `suspendida`,
`inconstitucional`) de cada norma citada en la fecha del devengo y bloquea
la aplicación cuando corresponde. Esto permite responder consultas
históricas con la versión de la norma que estaba viva en el momento del
hecho imponible.

La API HTTP carga el registry vía `load_norma_registry()` al construir el
app (`hacienda_ai.api.app.create_app`) y lo pasa al motor en cada
evaluación. Cada item de la respuesta `/evaluations` lleva un campo
`applicable_versions` con la redacción vigente en el devengo por cada
norma citada (deduplicada por `boe_id`).

## Límites

El motor solo evalúa datos estructurados. No interpreta texto libre ni
inventa importes.
