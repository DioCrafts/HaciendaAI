# Cálculo de la cuota IRPF

El módulo `src/hacienda_ai/tax_calculation.py` convierte el resultado del motor (lista de `RuleEvaluation`) en una **cuota líquida diferencial real** en euros. Sustituye el patrón anterior de "suma de importes de reglas que aplican" — que sobreestimaba el ahorro porque trataba reducciones de base y deducciones de cuota como equivalentes.

## Por qué es importante

Una reducción de 1.500 € en la base liquidable **no ahorra 1.500 €**: ahorra el tipo marginal aplicable × 1.500 €. Para un contribuyente con tipo marginal del 30 %, eso son ~450 €. Una deducción de 1.500 € en la cuota líquida **sí ahorra 1.500 €**.

Antes de este módulo, el motor sumaba ambos casos y reportaba "ahorraste 1.500 €". Ahora reporta la diferencia real de cuota diferencial entre dos escenarios.

## Flujo del cálculo (art. 56-68 LIRPF)

```
base_imponible_general (input)
  - reducciones (categoría REDUCCION del corpus)
  = base_liquidable_general

base_imponible_ahorro (input)
  = base_liquidable_ahorro

cuota_general = TARIFA_GENERAL(base_liquidable_general)
cuota_ahorro  = TARIFA_AHORRO(base_liquidable_ahorro)

# Doble escala del mínimo personal y familiar (art. 56.2)
mínimo_absorbido_general = min(mínimo, base_liquidable_general)
mínimo_remanente         = max(0, mínimo - mínimo_absorbido_general)
mínimo_absorbido_ahorro  = min(mínimo_remanente, base_liquidable_ahorro)

cuota_integra_general = cuota_general - TARIFA_GENERAL(mínimo_absorbido_general)
cuota_integra_ahorro  = cuota_ahorro  - TARIFA_AHORRO(mínimo_absorbido_ahorro)
cuota_integra_total   = cuota_integra_general + cuota_integra_ahorro

cuota_líquida = max(0,
    cuota_integra_total
    - deducciones_de_cuota    (categoría DEDUCCION del corpus, no bonificación)
    - bonificaciones_cuota    (calculation.type = cuota_bonification)
)

cuota_diferencial = cuota_líquida - retenciones   # puede ser negativa (a devolver)
```

## Mapeo de categorías del corpus

| Categoría de la regla | Aplicación |
| --- | --- |
| `REDUCCION` | Resta de `base_liquidable_general` |
| `DEDUCCION` (no `cuota_bonification`) | Resta de cuota líquida |
| `DEDUCCION` con `calculation.type=cuota_bonification` | Resta de cuota líquida (cubo "bonificaciones") |
| `GASTO_DEDUCIBLE` | **Ignorado**: se asume pre-descontado en `taxable_base.general` |
| `EXENCION` | Ignorado (out of scope MVP) |
| `MINIMO_PERSONAL_FAMILIAR` | Ignorado (lo calcula el motor nativo) |
| `COMPENSACION`, `AJUSTE` | Ignorado (out of scope MVP) |

Sólo se aplican las evaluaciones con `status == "applies"`. Las `missing_evidence` quedan fuera hasta que aporten los justificantes.

## Mínimos personales y familiares 2025

| Concepto | Importe |
| --- | --- |
| Mínimo del contribuyente | 5.550 € |
| Bonus si edad ≥ 65 | +1.150 € |
| Bonus adicional si edad ≥ 75 | +1.400 € |
| 1.º hijo | 2.400 € |
| 2.º hijo | 2.700 € |
| 3.º hijo | 4.000 € |
| 4.º hijo y siguientes | 4.500 € |
| Bonus por hijo < 3 años | +2.800 € |
| Ascendiente cualificante (≥ 65 o discapacidad) | 1.150 € |
| Bonus ascendiente ≥ 75 | +1.400 € |
| Discapacidad contribuyente ≥ 33 % | 3.000 € |
| Discapacidad contribuyente ≥ 65 % | 9.000 € |
| Bonus por ayuda de tercera persona | +3.000 € |

El wizard puede pasar un override directo en `family.personal_family_minimum_override` cuando el cálculo manual sea preferible.

## Tarifa estatal vs. autonómica

El motor calcula la cuota íntegra **separando explícitamente** la parte estatal de la autonómica. La cuota total es la suma de ambas escalas progresivas aplicadas en paralelo a la misma base liquidable.

| Componente | Constante | Tramos |
| --- | --- | --- |
| Estatal general | `STATE_GENERAL_TARIFF_2025` | 9,5 / 12 / 15 / 18,5 / 22,5 / 24,5 % |
| Autonómica genérica (= estatal) | `GENERIC_AUTONOMIC_GENERAL_TARIFF_2025` | idéntica a la estatal |
| Estatal ahorro | `STATE_SAVINGS_TARIFF_2025` | 9,5 / 10,5 / 11,5 / 13,5 / 15 % |
| Autonómica ahorro (simétrica por ley) | `AUTONOMIC_SAVINGS_TARIFF_2025` | idéntica a la estatal |

Cuando una CCAA no está registrada en `AUTONOMIC_GENERAL_TARIFFS`, se usa la genérica → la cuota total es **2 × estatal**.

### Cómo añadir la tarifa autonómica real de una CCAA

1. Localizar la norma autonómica que aprueba la tarifa (Decreto Legislativo o Ley autonómica de medidas fiscales) en el boletín oficial correspondiente para el ejercicio.
2. Crear una entrada en `AUTONOMIC_GENERAL_TARIFFS` con la `TaxScale` autonómica de esa CCAA. La selección es **case-insensitive** sobre `profile.region`.
3. Añadir un test en `tests/test_tax_calculation.py` que verifique las cifras esperadas (y la diferencia frente a la genérica).

El registry empieza **vacío** intencionalmente: añadir cifras de cada CCAA exige verificación.

## Limitaciones documentadas del MVP

- **Tarifa autonómica genérica**: 2 × estatal mientras el registry esté vacío para esa CCAA. La realidad fiscal es que las CCAA del régimen común tienen tarifas autonómicas ligeramente distintas; la "tarifa subsidiaria" del art. 65 LIRPF da aproximadamente 47 % en el tope (no 49 %). Hasta que cada CCAA tenga su entrada en `AUTONOMIC_GENERAL_TARIFFS`, el cálculo es una aproximación.
- **Discapacidad de descendientes/ascendientes**: el motor no la suma todavía al mínimo personal y familiar; usar `personal_family_minimum_override` mientras tanto.
- **Anualidades por alimentos, rentas en especie, imputación de rentas**: fuera del MVP.
- **Tarifa autonómica con anchos de tramos distintos**: algunas CCAA modifican los anchos. Soportado vía registry sin cambios de código.

## Uso

```bash
# CLI
hacienda-ai tax --profile profile.json
hacienda-ai tax --profile profile.json --format json

# API HTTP
curl -X POST http://localhost:8000/v1/tax -H 'Content-Type: application/json' -d @profile.json
```

Output del CLI con un perfil con donativos + plan de pensiones:

```
== Bases ==
  Base imponible general               35.000,00 €
  - Reducciones aplicadas               1.500,00 €
  Base liquidable general              33.500,00 €
  Base liquidable del ahorro            1.500,00 €

== Mínimo personal y familiar (doble escala) ==
  Mínimo aplicado                      10.750,00 €
  Cuota correspondiente al mínimo       2.042,50 €

== Cuota íntegra ==
  Tarifa general                        6.173,00 €
  Tarifa del ahorro                       285,00 €
  Total cuota íntegra                   6.458,00 €

== Deducciones de cuota ==
  Deducciones (art. 68 LIRPF)             220,00 €
  Cuota líquida                         6.238,00 €

== Resultado ==
  - Retenciones e ingresos a cuenta     5.800,00 €
  Cuota diferencial (a pagar)             438,00 €
```

## Tests

`tests/test_tax_calculation.py` cubre 25 casos: tarifa progresiva en cada tramo, doble escala con remanente sobre la base del ahorro, cada categoría del corpus aplicada en su sitio correcto, mínimos personales por composición familiar, y casos límite (cuota nunca negativa, devolución por retenciones).
