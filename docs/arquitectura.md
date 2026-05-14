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
