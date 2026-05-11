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
