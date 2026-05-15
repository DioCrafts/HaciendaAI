# Logging RGPD del motor

El motor de reglas (`src/hacienda_ai/rules.py`) emite trazas estructuradas a través de `src/hacienda_ai/logging_setup.py`. El diseño asume que estas trazas pueden acabar en sistemas de observabilidad de terceros (Datadog, Grafana, ELK, etc.) y por tanto **no debe filtrar datos personales del contribuyente**.

## Eventos emitidos

Tres eventos en el namespace `hacienda_ai.rules`:

| Evento | Nivel | Cuándo | Campos extra |
| --- | --- | --- | --- |
| `evaluate_started` | INFO | Al entrar en `evaluate_deductions()` | `tax_year`, `region_hash`, `filing_mode`, `deductions_count` |
| `rule_evaluated` | DEBUG | Una vez por cada regla evaluada | `deduction_id`, `status`, `missing_fields_count`, `missing_documents_count`, `has_amount` |
| `evaluate_finished` | INFO | Tras resolver incompatibilidades | `tax_year`, `region_hash`, `total`, `applies`, `missing_data`, `missing_evidence`, `pending_validation`, `does_not_apply` |

`rule_evaluated` es DEBUG porque genera un evento por regla y producirían volumen elevado en producción; útil para depuración local.

## Qué NO se loguea

- Importes (income, expenses, taxable_base, cuota, contribuciones).
- Importes calculados por la regla (`estimated_amount`). Sólo `has_amount: bool`.
- Nombres, NIFs o cualquier texto del perfil.
- Nombre de la CCAA en claro. Se sustituye por un `region_hash`.
- Texto literal de documentos requeridos.

## Hash de región

`hash_region()` produce un HMAC-SHA256 truncado a 8 caracteres hex:

- Sal: `secrets.token_bytes(16)` generada al **arrancar el proceso**. No persistente. Los hashes son **comparables sólo dentro de la misma ejecución**: dos sesiones distintas producirán hashes distintos para la misma región.
- Normaliza la entrada: `region.strip().lower()` antes del HMAC.
- Devuelve `"none"` cuando la región es `None` o cadena vacía.

Por qué esta opción y no un hash determinista cross-session: un hash determinista permitiría correlar logs históricos y reidentificar regiones poco pobladas (ej. Ceuta o Melilla) por frecuencia. La sal de proceso elimina ese vector.

## Configuración

Dos variables de entorno (o argumentos explícitos en `configure_logging()`):

| Variable | Valores | Default |
| --- | --- | --- |
| `HACIENDA_AI_LOG_LEVEL` | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` | `INFO` |
| `HACIENDA_AI_LOG_FORMAT` | `text` \| `json` | `text` |

`text` es el formato legible para terminal/CLI. `json` produce una línea JSON por evento, apta para ingesta por sistemas de observabilidad. El formatter JSON serializa todos los campos `extra` además de los estándar `ts`, `level`, `logger`, `event`.

```bash
HACIENDA_AI_LOG_LEVEL=DEBUG HACIENDA_AI_LOG_FORMAT=json hacienda-ai evaluate --profile profile.json
```

## Garantía vía tests

`tests/test_logging.py` mantiene una lista `SENSITIVE_LITERALS` con importes y texto del perfil de prueba. Para cada llamada a `evaluate_deductions()`, el test verifica que **ninguno de esos literales aparece en los logs serializados** (ni en text ni en JSON). Si en el futuro un cambio en el motor introduce un campo nuevo que filtre PII, ese test falla en CI.

## Limitaciones

- Sólo cubre el motor de reglas. El API HTTP (`api.py`) tiene logs de FastAPI/Uvicorn por defecto que sí pueden incluir información de las requests; si despliegas, recomendaría filtrarlos a nivel de proxy o configurar Uvicorn con `--log-config` desactivando los access logs.
- El campo `region_hash` permite distinguir distribuciones de uso por región (Madrid vs Cataluña genera hashes distintos consistentemente dentro de una sesión); si esto te preocupa para auditorías más estrictas, sustituye `hash_region` por una función constante.
- La sal de proceso vive en memoria: un dump del proceso podría exponerla. Para entornos hostiles, considera invalidarla periódicamente.
