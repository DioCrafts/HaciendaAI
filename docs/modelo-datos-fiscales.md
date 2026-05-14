# Modelo de datos fiscales

El modelo inicial está en `src/hacienda_ai/models.py` y cubre:

- Ejercicio fiscal.
- Comunidad autónoma.
- Modo de declaración.
- Datos personales.
- Familia.
- Ingresos.
- Retenciones.
- Gastos.
- Candidatos a deducción.
- Documentos justificativos.

La estructura se mantiene serializable a JSON para permitir API, tests, informes y futura persistencia.

## Campos del perfil usados por el corpus actual

Las deducciones del lote 1 (estatales 2025) consultan los siguientes campos del perfil fiscal:

| Ruta | Tipo | Usado por |
| --- | --- | --- |
| `income.work_income` | número | cuotas sindicales, cuotas colegios profesionales |
| `expenses.union_dues_amount` | número | cuotas sindicales |
| `expenses.professional_association_fees_amount` | número | cuotas colegios profesionales |
| `personal.professional_association_required` | booleano | cuotas colegios profesionales |
| `expenses.pension_plan_contribution_amount` | número | aportaciones plan de pensiones individual |
| `expenses.spouse_pension_plan_contribution_amount` | número | aportaciones plan de pensiones cónyuge |
| `family.spouse.work_income` | número | aportaciones plan de pensiones cónyuge |
| `expenses.donations_amount` | número | donativos Ley 49/2002 (lote 2) |
| `personal.donations_recurrent_qualifying` | booleano | donativos recurrentes (lote 2) |
| `taxable_base.liquidable` | número | tope del 10 % en donativos (lote 2) |
| `taxable_base.general` | número | tope alternativo `max_percentage_of_base_general` |
| `taxable_base.savings` | número | tope alternativo `max_percentage_of_base_savings` |
| `personal.is_eligible_maternity_deduction` | booleano | maternidad (lote 3) |
| `family.maternity_qualifying_child_months` | entero ≥ 0 | maternidad: suma de meses cualificantes a través de hijos elegibles (lote 3) |
| `personal.large_family_category` | "general" \| "especial" | familia numerosa (lote 3) |
| `family.large_family_qualifying_months` | entero 0-12 | familia numerosa: meses con título en vigor (lote 3) |
| `family.disabled_descendants_qualifying_months` | entero ≥ 0 | descendiente con discapacidad: suma de meses (lote 3) |
| `family.disabled_ascendants_qualifying_months` | entero ≥ 0 | ascendiente con discapacidad: suma de meses (lote 3) |

## Límites por base imponible

Cada deducción puede declarar `taxable_base_limits` con claves del conjunto:

- `max_percentage_of_base_liquidable` → consulta `taxable_base.liquidable` del perfil.
- `max_percentage_of_base_general` → consulta `taxable_base.general`.
- `max_percentage_of_base_savings` → consulta `taxable_base.savings`.

Los valores son porcentajes entre 0 y 1. El motor aplica el mínimo de todos los topes que estén declarados, además del `limit` global de la deducción. Si una deducción requiere un tope y el perfil no incluye la base correspondiente, el motor devuelve `missing_data` con la ruta exacta del campo ausente.

Si una regla no encuentra un campo obligatorio en el perfil, el motor responde `missing_data` indicando exactamente qué falta.
