# Golden dataset fiscal

`tests/golden/*.json` contiene **perfiles fiscales sintéticos con resultado
esperado por deducción y por importe**. Es la única red de seguridad
*fiscal* del repo: el resto de los tests verifican contratos de software
(status code, formato de payload, estabilidad de fingerprint…), no que el
motor calcule la cifra correcta.

El runner es `tests/test_golden.py`. Cada archivo del directorio se
ejecuta como un caso parametrizado de pytest.

## Qué garantiza

Para cada caso, el test comprueba:

1. **Conjunto exacto de `applies`** — la lista de deducciones aplicadas
   coincide bit a bit con `expected.applies`. Si añades una deducción al
   corpus y resulta que aplica para uno de estos perfiles, el test rompe
   y te obliga a actualizar el oracle conscientemente.
2. **Importe exacto** en cada `applies` — `estimated_amount` ==
   `expected.applies[i].amount`. Detecta regresión de fórmula (por
   ejemplo: alguien cambia el coeficiente de donativos sin querer) o
   modificación normativa legítima (BOE actualiza un importe y el cron
   `verify-seed` reporta drift).
3. **Conjunto exacto de `requires_manual_calculation`** — qué reglas
   aplican pero el motor no cuantifica todavía.
4. **Subsets opcionales** (`missing_data_includes`,
   `missing_evidence_includes`, `does_not_apply_includes`) para anclar
   señales negativas concretas sin amarrarse al tamaño total del corpus.

## Formato

```json
{
  "name": "Etiqueta humana del caso",
  "description": "Por qué este caso, qué cubre, qué deducciones son las relevantes.",
  "profile": { ... payload TaxProfile.from_dict ... },
  "expected": {
    "applies": [
      {"deduction_id": "es_X_2024", "amount": 1234.56, "justificacion": "art. X LIRPF"}
    ],
    "requires_manual_calculation": ["es_Y_2024"],
    "missing_data_includes": ["es_Z_2024"],
    "missing_evidence_includes": [],
    "does_not_apply_includes": ["es_W_2024"]
  }
}
```

El campo `justificacion` no lo valida el runner — sirve al humano que
audita el oracle.

## Cómo añadir un caso

1. Escribe el JSON con el perfil y un `expected` provisional o vacío.
2. Ejecuta este snippet desde el repo para ver qué dice el motor:

   ```python
   import json, sys; sys.path.insert(0, "src")
   from hacienda_ai.deductions import load_deductions
   from hacienda_ai.models import TaxProfile
   from hacienda_ai.normas import load_norma_registry
   from hacienda_ai.rules import evaluate_deductions

   raw = json.loads(open("tests/golden/XX_mi_caso.json").read())
   profile = TaxProfile.from_dict(raw["profile"])
   for ev in evaluate_deductions(load_deductions(), profile, load_norma_registry()):
       print(f"{ev.status:30s} {ev.deduction_id:60s} {ev.estimated_amount:.2f}")
   ```

3. **Audita los importes contra Manual Práctico Renta** antes de pegarlos
   al `expected`. El runner solo verifica que el motor y el oracle
   coinciden; si tú aceptas un número que el motor escupe sin
   contrastarlo, el oracle no defiende nada.
4. Corre `python -m pytest tests/test_golden.py::test_golden_case -q`.

## Cómo se actualiza tras un cambio normativo legítimo

1. El cron diario `.github/workflows/verify-seed.yml` detecta drift entre
   el corpus local y el texto BOE consolidado y abre issue automático.
2. Refresca el corpus: `python scripts/verify_seed.py --update`.
3. Los goldens afectados rompen aquí con diff claro (qué deducción, qué
   importe esperado, qué importe calcula el motor ahora).
4. Para cada golden roto: comprobar que el nuevo importe es coherente
   con el cambio normativo (Manual Práctico Renta del ejercicio
   relevante) y actualizar el `expected.applies[i].amount`.
5. Documentar en el commit qué norma cambió y por qué los importes se
   mueven (referencia al issue de `verify-seed`).

**No** actualices automáticamente con un script: cada cambio de cifra
oracle debe ir acompañado de un asesor o de la lectura directa de la
norma actualizada. Si el oracle se sincroniza sólo con el motor, el
oracle pierde el sentido.

## Casos actuales (índice)

| Archivo | Cubre |
|---|---|
| `01_empleado_madrid_1hijo_2024.json` | Empleado básico Madrid, mínimos personales y descendiente t1 |
| `02_pareja_conjunta_biparental_2hijos_2024.json` | Tributación conjunta biparental + descendientes t1+t2 |
| `03_monoparental_maternidad_2024.json` | Monoparental F + maternidad t1 + reducción art. 20 tramo bajo + monoparental |
| `04_discapacidad_severa_con_asistencia_2024.json` | Tres tramos del art. 60.1 LIRPF (base + ≥65 % + asistencia) |
| `05_familia_numerosa_especial_4hijos_2024.json` | Cuatro tramos descendientes + familia numerosa especial + conjunta biparental |
| `06_donativos_49_2002_fidelizado_2024.json` | QW7: escala 80/45 % de donativos con fidelización y cap dinámico BL |
| `07_dt18_vivienda_pre_2013_2024.json` | QW7: DT 18ª LIRPF al borde de base máxima 9 040 € |
| `08_eficiencia_energetica_rehabilitacion_2024.json` | QW7: DA 50ª.3 LIRPF 60 % con discriminador `energy_works_type` |
| `09_empleado_madrid_2hijos_2025.json` | Paridad corpus 2024↔2025: mismos importes |
| `10_empleado_sevilla_2024.json` | Filtro territorial: autonómicas Madrid no aplican en Sevilla |
| `11_joven_alquiler_madrid_2024.json` | Madrid art. 4 TRTC: 30 % de alquiler con cap 1 237,20 € |
| `12_devengo_historico_2018.json` | Filtro temporal: devengo pre-corpus no dispara nada |

## Criterio para añadir más casos

Cada caso nuevo debe cubrir **algo que los existentes no**: una rama del
motor (cálculo nuevo, filtro nuevo), una intersección de requisitos no
testada, o una deducción con riesgo de regresión por su complejidad
(escalada, tramificada, con discriminador, foral cuando aterrice…).

No tiene sentido tener 50 perfiles que ejercitan las mismas tres
deducciones de mínimos personales. La cobertura útil del golden es por
*rama* y por *intersección*, no por volumen.
