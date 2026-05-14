# Copiloto Fiscal IRPF España

Aplicación en construcción para ayudar a revisar oportunidades de optimización fiscal legal en la declaración de la renta española.

## Qué hace

- Normaliza deducciones, reducciones, gastos deducibles y ajustes fiscales en JSON auditable.
- Valida cada regla con un esquema estructurado.
- Evalúa reglas de forma determinista contra un perfil fiscal.
- Distingue entre:
  - aplica;
  - no aplica;
  - faltan datos;
  - falta documentación;
  - pendiente de validar.
- Rechaza solicitudes de evasión fiscal o falseamiento de datos.

## Qué no hace

- No sustituye a un asesor fiscal.
- No garantiza resultados.
- No presenta declaraciones en nombre del usuario.
- No recomienda ocultar ingresos, inventar gastos, manipular datos ni simular operaciones.
- No pide credenciales de Hacienda, Cl@ve, certificado digital ni banca online.

## Aviso legal

Esta herramienta ofrece ayuda informativa para revisar posibles oportunidades de optimización fiscal dentro de la legalidad. No sustituye a un asesor fiscal, no garantiza resultados y no presenta declaraciones en nombre del usuario. El usuario es responsable de verificar la información, conservar justificantes y revisar la declaración antes de presentarla.

La aplicación no ayuda con:

- ocultación de ingresos;
- facturas falsas;
- gastos inventados;
- simulación de residencia;
- uso indebido de familiares;
- manipulación de fechas;
- estructuras artificiosas sin sustancia;
- cualquier práctica contraria a la normativa tributaria.

## Instalación

El núcleo inicial no requiere dependencias de producción externas.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
```

## Uso del CLI

```bash
hacienda-ai evaluate --profile profile.json
hacienda-ai evaluate --profile profile.json --format json
hacienda-ai evaluate --profile profile.json --deductions ruta/a/deducciones.json
```

`profile.json` mínimo:

```json
{
  "tax_year": 2025,
  "region": "Madrid",
  "expenses": {"union_dues_amount": 250},
  "documents": ["Justificante de cuotas sindicales"]
}
```

El CLI imprime un resumen por estado (`Aplica`, `Falta documentación`, `Faltan datos`, `Pendiente de validación`, `No aplica`) y el importe estimado total. También puede invocarse como `python -m hacienda_ai evaluate ...`.

### Simulador

```bash
hacienda-ai simulate --profile profile.json
hacienda-ai simulate --profile profile.json --format json
```

El simulador genera tres escenarios sobre el mismo perfil:

- **conservador**: solo deducciones con requisitos cumplidos y justificantes aportados.
- **esperado**: añade deducciones a las que solo faltan justificantes documentales.
- **optimizado**: añade además deducciones a las que falta información estructurada del perfil.

Además repite la simulación cambiando `filing_mode` entre `individual` y `conjunta`, y sugiere el modo con mayor importe estimado bajo el escenario `esperado`. La sugerencia es informativa: no calcula la cuota IRPF completa ni sustituye al asesor.

## Ejecutar tests, lint y type checking

```bash
python -m pytest      # tests
ruff check .          # lint
ruff format --check . # formato
mypy                  # type checking estricto
```

Cada PR ejecuta automáticamente las cuatro comprobaciones en GitHub Actions (`.github/workflows/ci.yml`). Para reproducir el mismo control antes de cada commit:

```bash
pre-commit install
```

## Estructura del proyecto

```text
src/hacienda_ai/
  data/deductions/        # Deducciones normalizadas en JSON
  rag/                    # Estructura preparada para RAG jurídico
  deductions.py           # Carga y validación de deducciones
  models.py               # Modelos fiscales y esquema de deducciones
  rules.py                # Motor determinista de reglas
  safety.py               # Rechazo de solicitudes ilegales
docs/
  auditoria-repositorio.md
  arquitectura.md
  modelo-datos-fiscales.md
  motor-reglas.md
  fuentes-oficiales.md
  seguridad-privacidad.md
  roadmap.md
  como-ejecutar.md
tests/
  test_deductions.py
```

## Corpus de deducciones

Lote 1 (4 deducciones estatales 2025):

- `es_cuotas_sindicales_2025`: gasto deducible por cuotas sindicales (art. 19.2.a LIRPF).
- `es_cuotas_colegios_profesionales_2025`: gasto deducible por cuotas colegiales obligatorias, tope 500 € (art. 19.2.d LIRPF).
- `es_aportaciones_plan_pensiones_individual_2025`: reducción por aportaciones a plan de pensiones, tope 1.500 € (art. 52 LIRPF).
- `es_aportaciones_plan_pensiones_conyuge_2025`: reducción por aportaciones al plan del cónyuge si su renta es inferior a 8.000 €, tope 1.000 € (art. 51.7 LIRPF).

Lote 2 (2 deducciones estatales 2025, donativos Ley 49/2002 art. 19, tras Ley 7/2024):

- `es_donativos_no_recurrente_2025`: 80% sobre los primeros 250 € + 40% sobre el exceso.
- `es_donativos_recurrente_2025`: 80% sobre los primeros 250 € + 45% sobre el exceso si el contribuyente ha donado a la misma entidad importes iguales o superiores en los dos ejercicios anteriores. Incompatible con el régimen no recurrente; cuando ambas reglas serían aplicables, el motor selecciona la recurrente por mayor importe.

El límite global del 10% sobre la base liquidable está declarado en `taxable_base_limits` pero **aún no se aplica** por el motor; debe verificarse manualmente.

Todas las reglas están marcadas como `pendiente_tests`: tienen referencia legal y tests del motor, pero **requieren revisión fiscal humana contra el Manual práctico de Renta AEAT 2025** antes de promoverlas a `validada`. Mientras tanto, el motor las muestra como `Pendiente de validación` y no las recomienda directamente.

Para promover una regla: editar su `validation_status` en `src/hacienda_ai/data/deductions/` y poner `checked_at` y `last_reviewed_at` con la fecha de la revisión.

## Limitaciones actuales

- Las reglas del corpus están `pendiente_tests`; el motor no las recomienda hasta su revisión fiscal.
- No hay backend HTTP ni frontend todavía.
- No hay persistencia de perfiles ni documentos.
- El RAG jurídico está solo preparado a nivel de estructura de carpetas.
