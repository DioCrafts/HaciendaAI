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
  rules.py                # Motor determinista con filtro temporal
  safety.py               # Rechazo de solicitudes ilegales
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

- `GET  /`             — página de demo (HTML estático sin frameworks).
- `GET  /health`       — sonda de vida.
- `GET  /deductions`   — catálogo del corpus con citas pinpoint a BOE.
- `POST /profiles`     — valida y guarda un perfil fiscal en memoria.
- `GET  /profiles/{id}`— recupera un perfil guardado.
- `POST /evaluations`  — evalúa todas las deducciones contra un perfil
  guardado y devuelve estados + citas pinpoint + versión del corpus +
  disclaimer.

Sin persistencia: los perfiles viven en memoria por proceso. Reiniciar el
servidor los pierde. Es deliberado: la persistencia entra en una iteración
posterior.

## Verificación del corpus contra BOE

El corpus semilla (`src/hacienda_ai/data/deductions/2024_irpf_estatal.json`)
contiene 21 deducciones estatales del IRPF 2024 con `boe_id` real, pinpoint
de artículo y `content_hash` SHA-256 del texto normativo consolidado en BOE.

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

- El corpus semilla actual cubre únicamente deducciones **estatales** del
  IRPF 2024. Las autonómicas (BOCM, BOPV…) y forales no están todavía en
  el corpus: BOE no indexa esos textos consolidados y se necesitan
  lectores específicos por boletín oficial.
- La mayoría de los cálculos están marcados como `manual_review`. La cita
  legal está validada contra BOE; la cuantía aplicable requiere todavía
  revisión por asesor colegiado.
- No hay backend HTTP ni frontend todavía.
- No hay persistencia de perfiles ni documentos.
- El RAG jurídico está solo preparado a nivel de estructura de carpetas.
