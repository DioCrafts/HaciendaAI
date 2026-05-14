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

Si una regla no encuentra un campo obligatorio en el perfil, el motor responde `missing_data` indicando exactamente qué falta.
