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
