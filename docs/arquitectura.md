# Arquitectura objetivo

La aplicación se organizará como un núcleo fiscal determinista, una capa RAG documental y, en fases posteriores, API y frontend.

## Módulos

1. Modelo de hechos fiscales del usuario.
2. Motor de reglas fiscales.
3. Base de deducciones normalizada.
4. Simulador de declaración.
5. Optimizador legal.
6. Sistema de evidencias/documentación.
7. RAG jurídico con fuentes oficiales.
8. Interfaz conversacional con herramientas.
9. Panel de resultados.
10. Generador de informe auditable.

## Principio clave

El LLM no calcula ni decide por sí solo. Las recomendaciones deben proceder de reglas versionadas, fuentes recuperadas o estado explícito `pendiente_fuente`.

## API HTTP (FastAPI)

`src/hacienda_ai/api.py` expone el motor sobre HTTP cuando el extra `[api]` está instalado. Los endpoints (`/health`, `/v1/deductions`, `/v1/evaluate`, `/v1/simulate`) sólo serializan los dataclasses del núcleo; no hay lógica fiscal duplicada en la capa HTTP. `ValidationError` del motor se traduce a HTTP 400. El subcomando `hacienda-ai serve` arranca Uvicorn con import lazy: si el extra no está instalado, el CLI sale con código 2 y mensaje claro.

El módulo `api.py` no se importa desde el resto del paquete — el núcleo sigue funcionando sin FastAPI ni Uvicorn.

Auth opcional vía API key en header `X-API-Key`. Se activa definiendo la variable de entorno `HACIENDA_AI_API_KEY`. Sólo afecta a `/v1/*`; `/health` queda abierto.

## JSON Schema del corpus

`src/hacienda_ai/data/corpus.schema.json` (Draft 2020-12) describe la estructura de los ficheros del corpus. Está mantenido en paralelo a los dataclasses de `models.py`: cualquier cambio de schema requiere editar ambos sitios. El subcomando `hacienda-ai schema PATH...` valida ficheros contra el schema y la CI lo ejecuta sobre todo el corpus en cada PR. El test `tests/test_schema.py` blinda que el schema acepta el corpus actual y rechaza mutaciones inválidas conocidas, evitando deriva entre dataclasses y schema.

## Frontend (React + Vite)

`frontend/` contiene un cliente web mínimo en React 18 + TypeScript. No comparte código con Python: los tipos en `frontend/src/types.ts` son una réplica manual de los dataclasses del motor; cualquier cambio del modelo obliga a tocar ambos sitios. La capa `frontend/src/api.ts` envuelve los endpoints `/v1/*` con `fetch` nativo y tipa la respuesta. Sin estado global ni librerías de UI: solo `useState` + CSS plano con tema claro/oscuro automático.

## Tests E2E (Playwright)

`frontend/tests/e2e/` contiene tests Playwright que ejecutan el flujo real perfil → evaluate → simulate sobre Chromium headless. La configuración (`frontend/playwright.config.ts`) levanta dos `webServer` independientes (backend Python en :8000 y preview de Vite en :4173), espera a que ambos respondan y entonces corre los tests. La CI tiene un job `e2e` separado que instala backend + frontend, descarga Chromium con `playwright install --with-deps` y sube el reporte HTML como artefacto cuando algún test falla.
