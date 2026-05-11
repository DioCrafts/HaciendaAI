# Fuentes oficiales y RAG jurídico

La estructura preparada para RAG está en:

- `src/hacienda_ai/rag/ingestion/`
- `src/hacienda_ai/rag/retrieval/`
- `src/hacienda_ai/rag/sources/`

Fuentes objetivo:

- BOE.
- Ley y Reglamento del IRPF.
- Manuales prácticos de Renta AEAT.
- Normativa autonómica.
- Consultas vinculantes DGT.
- INFORMA AEAT.
- TEAC.
- Jurisprudencia relevante.

No se ha implementado ingesta todavía. Toda deducción sin fuente oficial debe conservar `validation_status = pendiente_fuente`.
