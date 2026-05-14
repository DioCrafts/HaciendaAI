# RAG jurídico (capa de fuentes oficiales)

## Qué es y qué NO es

El módulo `src/hacienda_ai/rag/` proporciona herramientas de **investigación legal**:

- Catálogo curado de fuentes oficiales (BOE, AEAT, boletines autonómicos).
- Descarga y caché local de esas fuentes.
- Búsqueda por palabra clave con snippets para localizar artículos relevantes.

**No genera reglas automáticamente.** Convertir el texto legal de una norma en un objeto `Deduction` con `validation_status: validada` que el motor utiliza para recomendar requiere **revisión fiscal humana por regla**. Esa barrera es intencional: es lo que separa la herramienta de un generador automático de "consejos fiscales" no auditables.

## Catálogo

`src/hacienda_ai/rag/sources/catalog.py` lista las fuentes con:

- `id`: identificador estable.
- `title`: nombre legible.
- `jurisdiction`: `"estatal"` o nombre de CCAA.
- `document_type`: `ley`, `real_decreto`, `decreto_legislativo`, `manual`, `consulta_dgt`, `norma_foral`, etc.
- `url`: enlace al BOE consolidado o al boletín autonómico.
- `notes`: contexto útil (versión, fecha de modificación, advertencias).

El catálogo actual es un punto de partida, NO una cobertura exhaustiva. Cubre la LIRPF y reglamento, mecenazgo (Ley 49/2002 + Ley 7/2024), Manual práctico AEAT y los textos refundidos de varias CCAA. Para añadir más fuentes, abrir un PR sobre `catalog.py`.

## CLI

```bash
pip install -e ".[rag]"

hacienda-ai rag list                                   # imprime el catálogo
hacienda-ai rag list --jurisdiction Madrid             # filtra por CCAA
hacienda-ai rag fetch --all                            # descarga todas las fuentes
hacienda-ai rag fetch --id es_lirpf --id es_reglamento_irpf
hacienda-ai rag status                                 # qué hay en caché local
hacienda-ai rag search "donativos recurrentes"        # búsqueda con snippets
```

La caché vive por defecto en `~/.cache/hacienda_ai/rag/`. Cada documento se almacena como `<id>.html` (o `.pdf`) con un `<id>.html.meta.json` adyacente que registra fecha de descarga, tamaño y `content-type`.

## Diseño

- **Fetcher** (`rag/ingestion/fetcher.py`): HTTP con `httpx`, User-Agent identificado, redirects automáticos, idempotencia (no re-descarga si ya está en caché salvo `--force`), pequeño delay entre fuentes para no martillear los servidores.
- **Extracción** (`rag/ingestion/text.py`): HTML → texto plano con BeautifulSoup, eliminando `script`, `style`, `nav`, etc. PDF queda explícitamente fuera del MVP (las fuentes prioritarias del catálogo son HTML consolidadas del BOE).
- **Búsqueda** (`rag/retrieval/search.py`): conteo de ocurrencias case-insensitive con snippets de contexto (~120 caracteres). Suficiente para un corpus pequeño (~15 documentos); si crece, sustituir por whoosh, rank_bm25 o un servicio externo.

## Flujo de promoción de una regla a `validada`

1. Identificar el artículo concreto (ej. LIRPF art. 81 bis para deducciones por familia numerosa).
2. `hacienda-ai rag fetch --id es_lirpf` para tenerlo en caché.
3. `hacienda-ai rag search "familia numerosa"` para localizar el texto exacto.
4. Verificar contra el Manual práctico de Renta del ejercicio (`hacienda-ai rag fetch --id es_aeat_manual_practico_renta`) — el manual es el documento de referencia operativa de la AEAT.
5. Editar el JSON de la regla en `src/hacienda_ai/data/deductions/`:
   - Añadir / completar `sources[]` con `url`, `checked_at` (fecha de la revisión) y `title` actualizado.
   - Ajustar `requirements`, `calculation`, `taxable_base_limits`, `effective_from`/`effective_to`.
   - Cambiar `validation_status` a `"validada"`.
   - Rellenar `last_reviewed_at`.
6. Añadir tests fiscales por regla siguiendo el patrón de `tests/test_corpus_lote1.py`.
7. CI valida el JSON contra `corpus.schema.json` y ejecuta los tests automáticamente.

## Por qué no automatizamos el paso 5

- La normativa fiscal cambia con leyes de PGE, leyes de medidas fiscales autonómicas y resoluciones de la DGT cada año.
- Un mismo artículo puede tener interpretaciones distintas según la doctrina administrativa.
- El motor presenta importes cuantificados al usuario; un error en una regla `validada` se traduce directamente en una recomendación fiscal incorrecta.
- La revisión humana por regla es una garantía no opcional.

## Caso de referencia: planes de pensiones individuales

La primera regla promovida a `validada` siguiendo este flujo es `es_aportaciones_plan_pensiones_individual_2025` (art. 52 LIRPF). La promoción exigió, además de actualizar `sources[].checked_at`:

1. **Modelar correctamente el doble límite del art. 52.1**: el motor sólo cubría caps absolutos (`limit`) y por porcentaje sobre las bases imponibles. El art. 52 introduce un cap del 30 % sobre los **rendimientos netos del trabajo + actividades económicas** — un concepto distinto de la base imponible. Para soportarlo se añadieron dos piezas:
   - Nueva clave `max_percentage_of_net_work_and_economic_income` en `taxable_base_limits`.
   - Nuevo campo del perfil `taxable_base.net_work_and_economic_income`.
2. **Tests fiscales** que verifican: aplica por debajo de ambos caps, recorta al 1.500 € absoluto, recorta al 30 % relativo, toma el menor de los dos cuando ambos se superan, devuelve `missing_data` si falta el dato del perfil.

Patrón a seguir para la siguiente regla: si el motor no soporta una restricción concreta, extender primero el motor y los tests, después promover la regla.
