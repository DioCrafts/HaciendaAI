"""Chunking jerárquico por artículo/apartado de normativa BOE.

Las leyes españolas tienen estructura jurídica estable que el retrieval
debe respetar: cortar a mitad de artículo o ignorar el contexto
("¿de qué Título estamos hablando?") produce alucinaciones. Este módulo:

1. **`hierarchy.py`**: lee el XML consolidado BOE y mantiene el contexto
   jerárquico (Título, Capítulo, Sección, Subsección) que envuelve a
   cada artículo. Esto vive en bloques `<bloque tipo="estructura">`,
   distintos de los `<bloque tipo="precepto">` que contienen el
   articulado.

2. **`splitter.py`**: divide el texto de un artículo en apartados
   numerados (`1.`, `2.`, ...) y letras (`a)`, `b)`, ...). Si el
   artículo no tiene apartados, se trata como un único apartado
   implícito.

3. **`builder.py`**: orquesta. Dado el XML consolidado de una norma,
   produce `IndexableChunk` por cada (artículo, apartado), con la
   metadata jerárquica completa pegada al chunk:

       {
         "boe_id": "BOE-A-2006-20764",
         "articulo": "art. 19",
         "apartado": "2.e)",
         "vigencia_desde": "2015-01-01",
         "vigencia_hasta": null,
         "jerarquia": ["Título III", "Capítulo I", "Sección 1ª"],
       }

   Estos chunks se inyectan en el RAG vector y permiten al LLM citar
   pinpoint sin inventar: `art. 19.2.e) LIRPF (BOE-A-2006-20764)`.

El módulo es independiente de `consolidated/articles.py` para no
acoplar la verificación de drift al chunking RAG, pero comparte las
regex de bloque/version vía import. Si cambia el formato del BOE, hay
un único punto de cambio.
"""

from __future__ import annotations

from .builder import (
    LegalChunkBuildError,
    build_legal_chunks,
    iter_norma_chunks_hierarchical,
)
from .hierarchy import (
    HierarchyContext,
    HierarchyKind,
    StructuralBlock,
    iter_structural_blocks,
)
from .splitter import (
    Apartado,
    split_article_into_apartados,
)

__all__ = [
    "Apartado",
    "HierarchyContext",
    "HierarchyKind",
    "LegalChunkBuildError",
    "StructuralBlock",
    "build_legal_chunks",
    "iter_norma_chunks_hierarchical",
    "iter_structural_blocks",
    "split_article_into_apartados",
]
