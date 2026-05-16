# Fuentes oficiales y RAG jurídico

## Corpus semilla (estado actual)

- `src/hacienda_ai/data/deductions/2024_irpf_estatal.json` — 34
  entradas estatales IRPF 2024 con `boe_id`, pinpoint de artículo y
  SHA-256 del texto normativo consolidado en BOE. Estado:
  `validation_status = validada` en todas. La mayoría usa
  `fixed_amount` o `percentage_with_cap` (mínimos personales/familiares
  tramificados, gasto del trabajo, reducción art. 20 tramo bajo,
  maternidad por hijo, familia numerosa, tributación conjunta,
  irregulares art. 18/32, inversión startups art. 68, pensiones
  individuales art. 51-52). QW7 incorpora además
  `tiered_progressive` para donativos Ley 49/2002 (escala 80 / 40 %,
  boost a 45 % por fidelización, cap dinámico 10 % base liquidable)
  y parte la entrada agregada de eficiencia energética en tres
  tramos calculables (DA 50ª.1 / .2 / .3) con discriminador
  `personal.energy_works_type`; la DT 18ª inversión vivienda
  habitual pasa a `percentage_with_cap` 15 % sobre base máxima
  9.040 €. Quedan en `manual_review` las reglas no lineales que el
  motor todavía no modela: arrendamiento art. 23.2, Ceuta/Melilla,
  régimen transitorio DT 15ª, encuadre general art. 56 y el tramo
  intermedio del art. 20 (14.852 €–19.747,5 €). Estas se surfacean
  como `requires_manual_calculation` (QW6) en lugar de
  `applies + 0 €`.
- `src/hacienda_ai/data/deductions/2025_irpf_estatal.json` — 34
  entradas estatales IRPF 2025 (Sprint 1 #1). Clon estructural del
  archivo 2024 con `tax_year=2025`, `effective_from=2025-01-01`,
  `effective_to=2025-12-31` y `last_reviewed_at` refrescado; los
  `content_hash` se conservan porque el verificador en vivo
  (`scripts/verify_seed.py`) reporta `drift=0`: el texto BOE
  consolidado de los preceptos LIRPF citados no se ha modificado entre
  el corte 2024 y el corte 2025. Como la LPGE 2025 no llegó a
  aprobarse y se ha venido prorrogando la del ejercicio anterior, los
  importes literales (mínimos, reducción art. 20 tramo bajo, gasto del
  trabajo, maternidad, familia numerosa, tributación conjunta)
  coinciden con 2024. Cuando una revisión futura detecte drift, el
  cron diario (QW3) abrirá issue automático y habrá que ajustar
  `fixed_amount`/`percentage` antes de mantener la entrada como
  `validada`.
- `src/hacienda_ai/data/deductions/2024_irpf_autonomico_madrid.json` —
  12 entradas autonómicas Comunidad de Madrid (Decreto Legislativo
  1/2010 con últimas modificaciones por Ley 13/2023): nacimiento /
  adopción, adopción internacional, acogimiento de menores y de
  mayores/discapacidad, arrendamiento vivienda habitual <35 años,
  gastos educativos, cuidado hijos <3, fomento autoempleo joven,
  inversión en empresas nuevas CM, donativos a fundaciones culturales
  CM, familias con dos o más descendientes y consumo cultural en
  Madrid. Anclaje con `boe_id="BOCM-..."` y `content_hash=null`: la API
  consolidada de BOE solo cubre normativa estatal, y un verificador
  específico BOCM queda como deuda explícita. El motor las acepta como
  `validation_status="validada"` por el prefijo `BOCM-`, distinto del
  régimen BOE estatal donde el `content_hash` SHA-256 sigue siendo
  obligatorio. Ver `is_state_bulletin_id` vs `is_regional_bulletin_id`
  en `models/_common.py` para la regla.
- `scripts/verify_seed.py` — verificador BOE. Descarga el texto
  consolidado vía la API abierta del BOE
  (`/datosabiertos/api/legislacion-consolidada/...`), selecciona la
  versión vigente en `last_reviewed_at`, excluye notas editoriales de
  modificación (`<p class="nota_pie*">`) y compara el hash SHA-256 con
  el declarado. Cron semanal en `.github/workflows/verify-seed.yml`.
  Solo procesa `boe_id` con prefijo `BOE-A-`; las fuentes BOCM y resto
  de boletines autonómicos se skip silenciosamente.

## Catálogo de normas vivas

- `src/hacienda_ai/data/normas/lirpf_versions.json` — siembra inicial
  con la historia agregada de la LIRPF en 3 ventanas sin solapamientos
  (redacción original 2007-2014, reforma Ley 26/2014 hasta 2021,
  redacción vigente desde 2022). Cubre devengos históricos a nivel de
  norma entera; la granularidad por artículo (p. ej. art. 20 según Ley
  31/2022) requiere extender el modelo con preceptos y queda pendiente.
- `src/hacienda_ai/data/normas/bocm_madrid_irpf.json` — Sprint 1 #2.
  Norma `BOCM-2010-258` (Decreto Legislativo 1/2010, Texto Refundido
  de tributos cedidos al Estado de la Comunidad de Madrid) con una
  ventana abierta `effective_from=2024-01-01` y `status=vigente` para
  cubrir los devengos 2024+ del corpus autonómico Madrid. Es la única
  norma autonómica que el corpus cita explícitamente hoy; con su
  registro, todas las deducciones `validada` quedan sometidas al filtro
  temporal por estado de norma y desaparece la WARN de QW1 sobre normas
  no registradas. Historias pre-2024 y modificadoras intermedias quedan
  pendientes hasta que el corpus incorpore esos devengos.
- `src/hacienda_ai/normas.py` — `load_norma_registry()`. Construye un
  `NormaRegistry` desde uno o varios JSON; el path por defecto está en
  `data/normas/` para que `pip install -e ".[api]"` sirva el corpus sin
  pasos adicionales. El registry se inyecta en el motor de reglas y la
  API enriquece cada evaluación con `applicable_versions` resuelto a la
  fecha del devengo del perfil.

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
