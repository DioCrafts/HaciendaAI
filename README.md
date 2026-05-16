# Copiloto Fiscal IRPF España

Motor determinista de reglas fiscales para el IRPF, con citas pinpoint
verificadas SHA-256 contra el BOE, filtro por vigencia temporal de las
normas citadas y respuesta histórica (la redacción aplicada es la que
estaba viva en la fecha del devengo, no la actual). Pensado como núcleo
auditable sobre el que añadir, en iteraciones posteriores, un RAG
jurídico, herramientas para gestorías y conectores con AEAT.

> **Estado actual** — prototipo. 44 deducciones IRPF 2024 ancladas a
> boletín oficial: 32 estatales con SHA-256 contra BOE y 12 autonómicas
> Comunidad de Madrid (Decreto Legislativo 1/2010) con anclaje BOCM
> pendiente de verificador SHA-256 específico. El motor calcula importe
> en las que tienen fórmula lineal/tramificada; el resto va en revisión
> manual. Historia de versiones agregada de la LIRPF en 3 ventanas
> (2007-2014 / 2015-2021 / 2022-hoy) y API HTTP de demostración con
> perfil en memoria. **No hay** RAG implementado, ni LLM integrado, ni
> multi-tenant, ni persistencia, ni cobertura foral, ni resto de CCAA.
> Ver `docs/roadmap.md` para el plan.

## Qué hace

- Normaliza deducciones, reducciones, gastos deducibles y ajustes fiscales en JSON auditable.
- Valida cada regla con un esquema estructurado.
- Evalúa reglas de forma determinista contra un perfil fiscal y devuelve
  importe estimado (cuando el cálculo es lineal o tramificado).
- Cita cada regla al BOE con pinpoint (artículo + apartado) y SHA-256
  del texto consolidado, verificado semanalmente por cron.
- Devuelve la versión de la norma vigente en la fecha del devengo, no
  solo la actual: bloquea aplicación si la norma está derogada o
  inconstitucional en esa fecha y degrada a `pending_validation` si está
  suspendida.
- Distingue entre:
  - aplica;
  - no aplica;
  - faltan datos;
  - falta documentación;
  - pendiente de validar.

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
python -m pip install -U pip pytest
```

## Ejecutar tests

```bash
python -m pytest
```

## Estructura del proyecto

```text
src/hacienda_ai/
  api/                    # FastAPI app + página estática de demo
  data/deductions/        # Corpus normalizado de deducciones (JSON)
  rag/                    # Estructura preparada para RAG jurídico
  models/                 # Esquema fiscal, Norma/VersionNorma, NormaRegistry
  deductions.py           # Carga y validación del corpus
  normas.py               # Carga del catálogo de normas y versiones
  rules.py                # Motor determinista con filtro temporal
scripts/
  verify_seed.py          # Verificador del corpus contra BOE (SHA-256)
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
  test_api.py
  test_deductions.py
  test_models.py
  test_norma.py
  test_verify_seed.py
```

## Demo HTTP

Arranca la API y la página estática de demo:

```bash
python -m pip install -e ".[api]"
python -m hacienda_ai.api --port 8000
```

Abre `http://127.0.0.1:8000/` en el navegador. La página rellena un perfil
sintético, lo envía a `POST /profiles` y luego a `POST /evaluations`, y
renderiza una tabla con estado, importe estimado, riesgo, motivo y enlaces
pinpoint clicables al BOE para cada deducción del corpus.

Endpoints disponibles:

- `GET  /`                          — página de demo (HTML estático).
- `GET  /health`                    — sonda de vida.
- `GET  /deductions`                — catálogo del corpus con citas pinpoint.
- `POST /profiles`                  — valida y guarda un perfil fiscal en memoria.
- `GET  /profiles/{id}`             — recupera un perfil guardado.
- `POST /evaluations`               — evalúa todas las deducciones contra un perfil
  guardado, persiste el resultado y devuelve `evaluation_id` + estados +
  citas pinpoint + `applicable_versions` (redacción vigente de cada norma
  citada en la fecha del devengo, con origen del cambio
  `modified_by_boe_id`) + disclaimer. Cambiar `devengo_date` en el perfil
  hace que la respuesta vuelva con la versión histórica de la norma que
  estaba viva entonces.
- `GET  /evaluations/{id}`          — recupera una evaluación previa.
- `GET  /evaluations/{id}/pdf`      — exporta la evaluación a PDF firmable
  para incorporar al expediente del cliente. El pie del PDF lleva el
  SHA-256 agregado del corpus + versión del motor + timestamp, de modo
  que cualquier cambio posterior del corpus modifica la firma. Citas BOE
  estatales clicables al texto consolidado; citas BOCM al consolidado de
  sede CM.

Sin persistencia: los perfiles viven en memoria por proceso. Reiniciar el
servidor los pierde. Es deliberado: la persistencia entra en una iteración
posterior.

## Verificación del corpus contra BOE

El corpus se distribuye en dos archivos:

- `src/hacienda_ai/data/deductions/2024_irpf_estatal.json` — 32 entradas
  estatales del IRPF 2024 con `boe_id` BOE-A real, pinpoint de artículo
  y `content_hash` SHA-256 del texto consolidado en BOE. Las entradas
  tramificadas (descendientes por orden, ascendientes ≥65/>75,
  discapacidad base/grado/asistencia, reducción art. 20 tramo bajo,
  maternidad por hijo, familia numerosa general/especial, tributación
  conjunta biparental/monoparental) reusan la misma cita BOE del
  artículo matriz y permiten al motor devolver importes calculados.
- `src/hacienda_ai/data/deductions/2024_irpf_autonomico_madrid.json` —
  12 entradas autonómicas de la Comunidad de Madrid (Decreto Legislativo
  1/2010, modificado por Ley 13/2023 y previas). Anclaje a BOCM con
  `boe_id="BOCM-..."`; el `content_hash` queda `null` hasta que se
  implemente un verificador BOCM dedicado — el motor las acepta como
  `validation_status="validada"` por el prefijo BOCM, distinto del
  régimen BOE estatal donde el hash es obligatorio.

Para reverificar la integridad de las citas:

```bash
python scripts/verify_seed.py
```

El verificador descarga la legislación consolidada vía la API abierta del
BOE, extrae cada artículo citado, selecciona la versión vigente en la fecha
de referencia (`last_reviewed_at` de la deducción), excluye notas
editoriales de modificación y compara el SHA-256 con el declarado. Sale `0`
sin drift, `1` con drift y `2` ante error de red o parsing. Un workflow
semanal (`.github/workflows/verify-seed.yml`) lo lanza los lunes.

## Limitaciones actuales

- El corpus cubre IRPF 2024 estatal completo y una selección autonómica
  Comunidad de Madrid (12 deducciones). Resto de CCAA y régimen foral
  (BOPV/BON) pendientes — BOE no indexa esos textos consolidados y se
  necesita un lector específico por boletín oficial.
- El verificador SHA-256 contra BOE consolidado (`scripts/verify_seed.py`)
  solo cubre fuentes con `boe_id` BOE-A-. Las citas autonómicas
  (`BOCM-...`) se almacenan con `content_hash=null` hasta que se
  implemente un verificador BOCM equivalente.
- Los importes lineales o tramificados publicados por AEAT (mínimos
  personales y familiares, gasto del trabajo, reducción art. 20 tramo
  bajo, maternidad, familia numerosa, tributación conjunta) ya los
  calcula el motor. Las reglas no lineales que escalan por base
  imponible (arrendamiento de vivienda art. 23.2, donativos Ley 49/2002,
  Ceuta/Melilla, eficiencia energética, regímenes transitorios
  DT 15ª/DT 18ª) siguen marcadas como `manual_review` hasta validación
  por asesor colegiado.
- No hay backend HTTP ni frontend todavía.
- No hay persistencia de perfiles ni documentos.
- El RAG jurídico está solo preparado a nivel de estructura de carpetas.
