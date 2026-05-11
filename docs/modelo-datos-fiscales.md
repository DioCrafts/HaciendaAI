# Modelo de datos fiscales

El modelo inicial está en `src/hacienda_ai/models.py` y cubre:

- Ejercicio fiscal.
- Comunidad autónoma.
- Modo de declaración.
- Datos personales.
- Familia.
- Ingresos.
- Retenciones.
- Gastos.
- Candidatos a deducción.
- Documentos justificativos.

La estructura se mantiene serializable a JSON para permitir API, tests, informes y futura persistencia.
