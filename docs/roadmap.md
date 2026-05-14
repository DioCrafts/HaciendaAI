# Roadmap

## Fase 1: Auditoría y normalización

- Auditoría del repositorio.
- Esquema normalizado de deducciones.
- Tests de validación y reglas.

## Fase 2: Motor de reglas

- Requisitos compuestos.
- Incompatibilidades.
- Evidencias por tipo documental.

## Fase 3: Simulador básico

- Escenarios conservador / esperado / optimizado implementados en `src/hacienda_ai/simulator.py`.
- Comparación individual vs conjunta mediante re-evaluación del perfil con `filing_mode` intercambiado y recomendación por importe estimado en el escenario `esperado`.
- Expuesto vía CLI: `hacienda-ai simulate --profile profile.json [--format json]`.
- Pendiente: cálculo real de cuota IRPF, mínimos personales y familiares, prorrateos.

## Fase 4: API

- FastAPI expuesto en `src/hacienda_ai/api.py` con `/health`, `/v1/deductions`, `/v1/evaluate`, `/v1/simulate`.
- CLI `hacienda-ai serve` arranca Uvicorn con import lazy (extra `[api]`).
- `ValidationError` del motor → HTTP 400 con detalle. OpenAPI/Swagger UI automáticos.
- Pendiente: endpoints de informes generados, auth/API keys, persistencia de perfiles, despliegue.

## Fase 5: Frontend

- Frontend mínimo en `frontend/` (React 18 + TypeScript + Vite) que consume `/v1/evaluate`, `/v1/simulate` y `/v1/deductions`.
- Single-page con barra de configuración del API (URL base + X-API-Key opcional), formulario estructurado del perfil con un botón "Cargar perfil de ejemplo" y dos pestañas (Evaluación / Simulación) con totales agrupados por estado.
- Tests E2E con Playwright + Chromium en `frontend/tests/e2e/`; job `e2e` independiente en GitHub Actions que arranca backend + frontend antes de ejecutar.
- Pendiente: wizard guiado por pasos, persistencia local del perfil, soporte multi-idioma, edición avanzada en JSON crudo.

## Fase 6: RAG jurídico

- Catálogo curado de fuentes oficiales (BOE LIRPF/RIRPF, Ley 49/2002, Ley 7/2024, Manual AEAT, textos refundidos autonómicos) en `src/hacienda_ai/rag/sources/catalog.py`.
- Fetcher con caché local (`~/.cache/hacienda_ai/rag/`), extracción HTML→texto, búsqueda por palabra clave con snippets.
- CLI `hacienda-ai rag list/fetch/status/search`.
- **No auto-genera reglas**: la promoción a `validation_status: validada` sigue requiriendo revisión humana por regla.
- Pendiente: parseo PDF, BM25/embeddings reales, ingestión de consultas vinculantes de la DGT, paginación de los resultados de búsqueda.

## Fase 7: Endurecimiento

- RGPD, seguridad, observabilidad sin PII y revisión fiscal.
