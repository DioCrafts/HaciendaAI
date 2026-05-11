# Auditoría inicial del repositorio

Fecha de auditoría: 2026-05-11.
Repositorio auditado: `/workspace/HaciendaAI`.

## 1. Stack detectado

- **Lenguaje detectado al inicio:** ninguno. El repositorio solo contenía `README.md`.
- **Framework:** ninguno.
- **Sistema de paquetes:** ninguno.
- **Base de datos:** no existe.
- **Frontend:** no existe.
- **Backend:** no existe.
- **Tests existentes:** no existían tests.
- **Estructura inicial:**
  - `README.md`
  - `.git/`

Conclusión: el repositorio no contenía una aplicación ejecutable ni una base de deducciones versionada. Para avanzar de forma mínima y no destructiva se ha creado un núcleo Python sin dependencias externas obligatorias, con modelos tipados mediante `dataclasses`, validación explícita y tests con `pytest`.

## 2. Estado de la base de deducciones

No se encontró una base de deducciones previa. La única información versionada era el título del proyecto en el `README.md`.

Estado tras la primera normalización mínima:

- **Ubicación nueva:** `src/hacienda_ai/data/deductions/`.
- **Formato:** JSON con clave raíz `deductions`.
- **Separación por ejercicio fiscal:** preparada mediante archivos por año; se ha creado `2025_pending_seed.json`.
- **Separación por comunidad autónoma:** preparada mediante campos `scope` y `region`.
- **Límites:** el esquema admite `limit`, `taxable_base_limits` y límites internos de cálculo, pero las semillas no inventan porcentajes ni topes.
- **Requisitos:** el esquema admite requisitos estructurados `field/operator/value`.
- **Fuente normativa:** obligatoria como lista `sources`; cuando no hay fuente validada se marca como `pendiente_validacion`.
- **Documentación necesaria:** obligatoria como lista `required_documents`.
- **Incompatibilidades:** admitidas como lista de ids.
- **Tests:** se han añadido tests de carga, validación y motor de reglas.
- **Casillas de Renta WEB:** admitidas como `rent_web_boxes`, vacías hasta validación oficial.

## 3. Riesgos detectados

- **Base fiscal inexistente:** no había deducciones previas que aprovechar.
- **Riesgo de alucinación normativa:** cualquier deducción real debe incorporarse solo con fuente oficial y revisión por ejercicio fiscal.
- **Sin vigencia validada:** las semillas creadas quedan con `effective_from`, `effective_to` y `last_reviewed_at` a `null` y `validation_status = pendiente_fuente`.
- **Sin casillas Renta WEB:** no se han añadido casillas por no estar verificadas.
- **Sin porcentajes ni límites oficiales:** no se han añadido importes no contrastados.
- **Reglas incompletas para producción:** las semillas sirven para probar estructura, no para recomendar aplicación directa.
- **Falta de backend/frontend:** no hay API ni interfaz todavía.
- **Falta de persistencia y RGPD:** no existe gestión de usuarios, borrado ni exportación porque aún no hay almacenamiento.
- **RAG jurídico pendiente:** se ha creado la estructura objetivo, pero no hay ingesta ni recuperación implementadas.

## 4. Plan de refactor mínimo

1. Mantener el repositorio como núcleo auditable antes de construir UI.
2. Consolidar `src/hacienda_ai/models.py` como contrato de deducciones y perfil fiscal.
3. Añadir un proceso de ingesta que solo acepte deducciones con fuentes oficiales o las marque como `pendiente_fuente`.
4. Separar datos fiscales del usuario, reglas y fuentes documentales.
5. Añadir tests por cada deducción validada antes de exponerla al usuario como aplicable.
6. Incorporar API FastAPI solo cuando el contrato de modelos y el motor de reglas estén estabilizados.
7. Construir el frontend después de disponer de evaluación fiable de oportunidades fiscales.

## 5. Estructura normalizada propuesta para deducciones

Campos mínimos:

- `id`: identificador estable y único.
- `name`: nombre.
- `description`: descripción clara.
- `tax_year`: ejercicio fiscal.
- `scope`: `estatal`, `autonomico` o `local`.
- `region`: comunidad autónoma si aplica.
- `category`: `deduccion`, `reduccion`, `exencion`, `gasto_deducible`, `minimo_personal_familiar`, `compensacion` o `ajuste`.
- `requirements`: requisitos estructurados.
- `calculation`: fórmula determinista o `manual_review`.
- `limit`: límite general, si está validado.
- `taxable_base_limits`: límites de base imponible.
- `incompatibilities`: deducciones incompatibles.
- `required_documents`: justificantes necesarios.
- `rent_web_boxes`: casillas conocidas y verificadas.
- `sources`: fuentes oficiales o marca explícita de pendiente de fuente.
- `effective_from` / `effective_to`: vigencia.
- `last_reviewed_at`: última revisión.
- `risk_level`: `bajo`, `medio` o `alto`.
- `validation_status`: `validada`, `pendiente_fuente`, `pendiente_tests`, `obsoleta` o `dudosa`.

## 6. Primera mejora implementada

- Se añadió un esquema de deducción normalizado.
- Se añadió un modelo de perfil fiscal mínimo.
- Se añadió un cargador de deducciones JSON con detección de ids duplicados.
- Se añadió un motor de reglas determinista que distingue `applies`, `does_not_apply`, `missing_data`, `missing_evidence` y `pending_validation`.
- Se añadió un filtro básico de seguridad para rechazar solicitudes de evasión fiscal.
- Se añadieron pruebas unitarias iniciales.
