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

## Limitaciones actuales

- La auditoría inicial no encontró una base de deducciones previa en el repositorio.
- Las deducciones semilla están marcadas como `pendiente_fuente` y no deben recomendarse directamente.
- No hay backend HTTP ni frontend todavía.
- No hay persistencia de perfiles ni documentos.
- El RAG jurídico está solo preparado a nivel de estructura de carpetas.
