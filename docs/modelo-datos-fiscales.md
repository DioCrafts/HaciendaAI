# Modelo de datos fiscales

El modelo vive en el paquete `src/hacienda_ai/models/`:

- `schema.py` — `Deduction`, `Source`, `Requirement`, `Calculation`,
  `RuleEvaluation` y `TaxProfile`, además de los enums de `Scope`,
  `ForalTerritory`, `DeductionCategory`, `RiskLevel` y `ValidationStatus`.
- `norma.py` — `Norma`, `VersionNorma`, `NormaStatus`, `NormaRegistry` y
  `SourceKind`. Separa la identidad de la norma (`Norma.boe_id`) de su
  evolución temporal (`VersionNorma`), de modo que se puede responder con
  precisión a "¿qué decía esta norma en marzo de 2023?".
- `_common.py` — validadores compartidos (fechas ISO 8601, SHA-256 hex,
  etc.) y `ValidationError`.

El perfil fiscal (`TaxProfile`) cubre ejercicio, comunidad autónoma, fecha
del devengo opcional, modo de declaración, datos personales, familia,
ingresos, retenciones, gastos, candidatos a deducción y documentos
justificativos. Cuando no se especifica `devengo_date`, el motor asume el
31 de diciembre del `tax_year` (regla general IRPF).

La estructura se mantiene serializable a JSON para permitir API, tests,
informes y futura persistencia.
