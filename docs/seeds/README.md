# Listas de siembra del corpus (CENDOJ, DGT, TEAC)

Estos ficheros sirven como **input para los workflows manuales** de ingesta
(`ingest-cendoj.yml`, `ingest-dgt.yml`, `ingest-teac.yml`). CENDOJ, Petete
(DGT) y DYCTEA (TEAC) no exponen API REST oficial ni autorizan scraping
masivo, por lo que la siembra del corpus es necesariamente humana: un
operador localiza las referencias relevantes en los buscadores oficiales y
las pasa al workflow.

## Estructura

Cada fichero tiene una entrada por línea (las líneas vacías y las que
empiezan por `#` se ignoran — útil para agrupar por tema):

- `cendoj-eclis.txt` — un ECLI por línea (`ECLI:ES:TS:2024:1234`).
- `dgt-consultas.txt` — un número por línea (`V0123-24`).
- `teac-resoluciones.txt` — un número por línea (`00/12345/2023` o `R.G. 67890/2022`).

## Cómo usarlos

1. Abre el fichero, descomenta o añade las referencias que quieres ingestar.
2. Copia las líneas activas (sin `#`) y pégalas separadas por comas en el
   input `numeros`/`eclis` del workflow correspondiente al disparar
   `workflow_dispatch`.
3. El workflow abrirá un PR con los JSON parseados; revísalo y mergéalo.
4. El merge a `main` dispara automáticamente `index-vector-store.yml`, que
   reindexa el corpus en el vector store (hoy `hash`+`memory`; cuando
   configures `VOYAGE_API_KEY` y `QDRANT_URL` como secrets, promociona el
   default en ese workflow para activar Voyage+Qdrant reales).

## Filosofía

**No inventamos referencias.** Cada ECLI/número que aparece descomentado en
estos ficheros es un hito muy citado en la doctrina tributaria española,
verificable directamente en el buscador del CGPJ, Petete o DYCTEA. Para
todos los demás temas listados, se proporcionan **criterios de búsqueda**
(palabras clave, rangos de fecha, órganos) que el operador ejecuta en el
portal oficial para localizar el número exacto antes de añadirlo aquí.

Este es el mismo principio anti-alucinación que aplica el resto del
sistema: un número de sentencia inventado contamina el corpus para siempre.

## Cobertura objetivo

Para un asesor fiscal competente, el corpus mínimo razonable es:

- **CENDOJ**: ~50 sentencias TS + ~20 AN + ~30 TSJ relevantes, repartidas
  entre IRPF (rendimientos, ganancias, deducciones, residencia), IVA
  (regla de la prorrata, intracomunitarias, exenciones), IS (operaciones
  vinculadas, BINs, libertad de amortización), IIVTNU post-STC 182/2021,
  ITP-AJD, y procedimiento (LGT).
- **DGT**: ~80 consultas vinculantes recientes (últimos 3 años) con el
  criterio doctrinal vigente para los supuestos más consultados.
- **TEAC**: ~30 resoluciones, priorizando las de unificación de criterio
  (art. 242 LGT) y extensión de efectos (art. 244 LGT), por su carácter
  vinculante para la AEAT.

Los ficheros en este directorio son el **arranque** (~10-15 referencias
verificadas por fuente + 60-80 temas con búsquedas dirigidas). Lo
correcto es ampliarlos progresivamente conforme se ingesten lotes
revisados.
