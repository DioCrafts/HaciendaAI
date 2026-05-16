# Fuentes oficiales y RAG jurídico

## Corpus semilla (estado actual)

- `src/hacienda_ai/data/deductions/2024_irpf_estatal.json` — 21
  deducciones estatales IRPF 2024 con `boe_id`, pinpoint de artículo y
  SHA-256 del texto normativo consolidado en BOE. Estado:
  `validation_status = validada` en todas. Cálculos cuantitativos en
  `manual_review` salvo cuando el importe es estable (mínimo del
  contribuyente, gasto deducible general del trabajo).
- `scripts/verify_seed.py` — verificador BOE. Descarga el texto
  consolidado vía la API abierta del BOE
  (`/datosabiertos/api/legislacion-consolidada/...`), selecciona la
  versión vigente en `last_reviewed_at`, excluye notas editoriales de
  modificación (`<p class="nota_pie*">`) y compara el hash SHA-256 con
  el declarado. Cron semanal en `.github/workflows/verify-seed.yml`.

## Estructura objetivo (pendiente)

- `src/hacienda_ai/rag/ingestion/`
- `src/hacienda_ai/rag/retrieval/`
- `src/hacienda_ai/rag/sources/`

Fuentes objetivo a incorporar:

- BOE (estatal): ya parcialmente cubierto por el verificador, falta el
  resto de la LIRPF, LGT y leyes conexas, así como la indexación
  full-text para retrieval.
- Manuales prácticos de Renta AEAT.
- Normativa autonómica: requiere lector BOCM, DOGC, DOG, BOPV… (no
  indexada en la API de BOE consolidada).
- Forales: BON (Navarra), BOPV/BOB/BOG (País Vasco).
- Consultas vinculantes DGT.
- INFORMA AEAT.
- TEAC.
- Jurisprudencia relevante (CENDOJ).

Toda deducción sin fuente oficial validada debe conservar
`validation_status = pendiente_fuente`. Las normas autonómicas/forales
que se incorporen en el futuro usarán identificadores propios del
boletín correspondiente (p. ej. `BOCM-...`, `BOPV-...`); el modelo
`Source.boe_id` admite ese prefijo y el verificador debe extenderse con
adaptadores específicos por boletín.
