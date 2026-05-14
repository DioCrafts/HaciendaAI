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

- Wizard fiscal y panel de oportunidades.

## Fase 6: RAG jurídico

- Ingesta y búsqueda filtrada de fuentes oficiales.

## Fase 7: Endurecimiento

- RGPD, seguridad, observabilidad sin PII y revisión fiscal.
