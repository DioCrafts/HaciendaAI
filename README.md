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

### API HTTP

```bash
pip install -e ".[api]"             # extras HTTP (FastAPI + Uvicorn)
hacienda-ai serve --port 8000       # arranca el servidor

# Con auth (X-API-Key header obligatorio en /v1/*):
hacienda-ai serve --api-key 'mi-clave-secreta'
# o equivalentemente:
HACIENDA_AI_API_KEY='mi-clave-secreta' hacienda-ai serve
```

Endpoints:

- `GET /health` — liveness y versión del paquete (siempre abierto).
- `GET /v1/deductions?region=Madrid&tax_year=2025` — resumen del corpus, opcionalmente filtrado por CCAA y/o ejercicio.
- `POST /v1/evaluate` — body = perfil fiscal JSON; devuelve la lista de `RuleEvaluation`.
- `POST /v1/simulate` — body = perfil fiscal JSON; devuelve la simulación completa (3 escenarios × 2 modos de tributación + modo recomendado).
- `GET /docs` y `GET /openapi.json` — OpenAPI / Swagger UI automáticos de FastAPI.

`ValidationError` del motor se traduce a HTTP 400 con el detalle del campo problemático.

**Autenticación**: si la variable de entorno `HACIENDA_AI_API_KEY` está definida, todos los endpoints `/v1/*` exigen el header `X-API-Key` coincidente. Si no está definida, la API funciona abierta (modo de desarrollo). `/health` queda siempre accesible (útil para monitores y load balancers). La comparación se hace con `secrets.compare_digest` para evitar ataques de timing.

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
python -m mypy        # type checking estricto
```

Validar ficheros del corpus contra el JSON Schema:

```bash
hacienda-ai schema src/hacienda_ai/data/deductions/*.json
```

El schema vive en `src/hacienda_ai/data/corpus.schema.json` (Draft 2020-12). Los editores con soporte de JSON Schema (VSCode, JetBrains) pueden referenciarlo para obtener autocompletado e inline-validation al editar reglas.

Cada PR ejecuta automáticamente las cinco comprobaciones en GitHub Actions (`.github/workflows/ci.yml`): ruff lint, ruff format, mypy strict, pytest y validación del corpus contra el JSON Schema. Para reproducir el mismo control antes de cada commit:

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

El límite global del 10 % sobre la base liquidable se aplica por el motor a partir de `taxable_base.liquidable` en el perfil. Si el perfil no incluye `taxable_base.liquidable`, el motor devuelve `missing_data` indicando el campo concreto.

Lote 3 (5 deducciones estatales 2025, prorrateadas por meses):

- `es_maternidad_2025`: 100 €/mes por hijo menor de 3 años elegible (1.200 € anuales por hijo, sin tope global de meses, art. 81 LIRPF + Ley 6/2023).
- `es_familia_numerosa_general_2025`: 100 €/mes hasta 1.200 € anuales (art. 81 bis LIRPF). Incompatible con la categoría especial.
- `es_familia_numerosa_especial_2025`: 200 €/mes hasta 2.400 € anuales. Incompatible con la categoría general.
- `es_descendiente_discapacidad_2025`: 100 €/mes por descendiente con discapacidad elegible.
- `es_ascendiente_discapacidad_2025`: 100 €/mes por ascendiente con discapacidad elegible.

Los campos de meses (`family.maternity_qualifying_child_months`, etc.) se entienden como suma de meses elegibles a través de todos los hijos/ascendientes; el wizard que recoja datos debe normalizarlos. Los complementos por hijo adicional en familia numerosa (600 €/hijo a partir del 4º o 6º) no están modelados todavía.

Corpus autonómico base (15 CCAA de régimen común, 1 placeholder por comunidad):

Cubre las 15 CCAA de régimen común con la deducción autonómica por arrendamiento de vivienda habitual para jóvenes. Todas en `pendiente_fuente`: el motor las carga y filtra por región pero NO las recomienda hasta que un asesor contraste porcentaje, edad y tope contra la normativa autonómica vigente. País Vasco y Navarra quedan fuera (régimen foral). Ceuta y Melilla quedan fuera (aplican bonificación general del 60 %). Detalle en `docs/corpus-autonomico.md`.

Todas las reglas están marcadas como `pendiente_tests`: tienen referencia legal y tests del motor, pero **requieren revisión fiscal humana contra el Manual práctico de Renta AEAT 2025** antes de promoverlas a `validada`. Mientras tanto, el motor las muestra como `Pendiente de validación` y no las recomienda directamente.

Para promover una regla: editar su `validation_status` en `src/hacienda_ai/data/deductions/` y poner `checked_at` y `last_reviewed_at` con la fecha de la revisión.

## Limitaciones actuales

- Las reglas del corpus están `pendiente_tests`; el motor no las recomienda hasta su revisión fiscal.
- No hay backend HTTP ni frontend todavía.
- No hay persistencia de perfiles ni documentos.
- El RAG jurídico está solo preparado a nivel de estructura de carpetas.
