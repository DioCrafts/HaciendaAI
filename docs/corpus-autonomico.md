# Corpus autonómico

El corpus autonómico inicial (`src/hacienda_ai/data/deductions/2025_autonomicas_base.json`) cubre **las 15 CCAA de régimen común** con una única deducción placeholder por comunidad: la deducción autonómica por arrendamiento de vivienda habitual para jóvenes (la más extendida entre territorios, con variaciones de porcentaje, edad y tope).

## CCAA cubiertas

| CCAA | ID de la regla | Edad límite | % placeholder | Cap placeholder (€) |
| --- | --- | --- | --- | --- |
| Andalucía | `auto_andalucia_alquiler_jovenes_2025` | < 35 | 15 % | 600 |
| Aragón | `auto_aragon_alquiler_jovenes_2025` | < 35 | 10 % | 500 |
| Asturias | `auto_asturias_alquiler_jovenes_2025` | < 35 | 10 % | 500 |
| Illes Balears | `auto_baleares_alquiler_jovenes_2025` | < 36 | 15 % | 530 |
| Canarias | `auto_canarias_alquiler_jovenes_2025` | < 35 | 20 % | 600 |
| Cantabria | `auto_cantabria_alquiler_jovenes_2025` | < 35 | 10 % | 300 |
| Castilla-La Mancha | `auto_castilla_la_mancha_alquiler_jovenes_2025` | < 36 | 15 % | 450 |
| Castilla y León | `auto_castilla_y_leon_alquiler_jovenes_2025` | < 36 | 20 % | 459 |
| Cataluña | `auto_cataluna_alquiler_jovenes_2025` | < 33 | 10 % | 300 |
| Madrid | `auto_madrid_alquiler_jovenes_2025` | < 35 | 30 % | 1 237,20 |
| Comunitat Valenciana | `auto_valenciana_alquiler_jovenes_2025` | < 35 | 15 % | 550 |
| Extremadura | `auto_extremadura_alquiler_jovenes_2025` | < 36 | 10 % | 400 |
| Galicia | `auto_galicia_alquiler_jovenes_2025` | < 36 | 10 % | 300 |
| La Rioja | `auto_la_rioja_alquiler_jovenes_2025` | < 36 | 10 % | 300 |
| Murcia | `auto_murcia_alquiler_jovenes_2025` | < 35 | 10 % | 300 |

> ⚠️ Los porcentajes, edades y topes anteriores son **placeholders estructurales**, no datos fiscales contrastados. Todas las reglas están marcadas como `validation_status: pendiente_fuente`, por lo que el motor las carga y filtra por región pero **NO las recomienda**. Para promover una regla a `validada`, contrastar con la normativa autonómica vigente, ajustar los valores y cambiar `validation_status` en el JSON.

## CCAA / ciudades autónomas no cubiertas

- **País Vasco**: régimen foral (Diputaciones Forales de Álava, Bizkaia y Gipuzkoa). El IRPF no se rige por la Ley 35/2006; cada Territorio Histórico tiene su propia normativa foral. No procede modelarlo como una deducción autonómica del régimen común.
- **Comunidad Foral de Navarra**: régimen foral propio. Mismo motivo.
- **Ceuta** y **Melilla**: no aplican deducciones autonómicas como tales, pero sí la bonificación del 60 % sobre la cuota íntegra correspondiente a rentas obtenidas en esas ciudades (art. 68.4 LIRPF). Ya están en el corpus como reglas **estatales con region asignada** (`es_bonificacion_ceuta_2025` y `es_bonificacion_melilla_2025`), usando el tipo de cálculo `cuota_bonification`. El motor aplica el 60 % al campo `cuota.attributable_to_ceuta_melilla` del perfil; la atribución la calcula el wizard/asesor. Estado: `pendiente_tests` mientras la atribución no esté contrastada por un humano.

## Flujo de promoción de una regla a producción

1. Identificar la regla en `2025_autonomicas_base.json`.
2. Contrastar contra la normativa autonómica vigente en el ejercicio correspondiente (BOE consolidado de cada CCAA o web tributaria).
3. Ajustar `requirements`, `calculation.percentage`, `calculation.cap`, edad límite, etc.
4. Sustituir la fuente `pendiente_validacion` por una referencia legal real con `url` al BOE/DOG/DOGC/BOJA/etc. y `checked_at` con la fecha de la revisión.
5. Cambiar `validation_status` a `validada` y rellenar `last_reviewed_at`.
6. Añadir un test fiscal por regla (siguiendo el patrón de `tests/test_corpus_lote1.py`).

Una vez en `validada`, el motor empezará a recomendar la regla con el importe calculado.
