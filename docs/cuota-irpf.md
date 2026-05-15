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

## Limitaciones documentadas del MVP

- **Tarifa autonómica = estatal genérica**: la suma agregada (19/24/30/37/45/47 % en la general y 19/21/23/27/30 % en la del ahorro) refleja la mayoría de CCAA del régimen común para 2025. CCAAs con tarifas más altas (algunas tramos de Cataluña, Comunitat Valenciana) o más bajas (Madrid en algunos tramos) producirán desviaciones del orden del 1-3 % de cuota. Roadmap: parametrizar por CCAA.
- **Discapacidad de descendientes/ascendientes**: el motor no la suma todavía al mínimo personal y familiar; usar `personal_family_minimum_override` mientras tanto.
- **Anualidades por alimentos, rentas en especie, imputación de rentas**: fuera del MVP.
- **Tarifa autonómica con anchos de tramos distintos**: algunas CCAA modifican los anchos. Misma observación.

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
