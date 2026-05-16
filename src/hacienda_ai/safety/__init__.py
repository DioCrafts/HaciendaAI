"""Capa de seguridad: guard rails anti-alucinación para texto generado.

Hoy contiene un único verificador, `citation_guard`, que cruza las citas
legales presentes en un texto contra el corpus auditable y el registro de
normas. Es la pieza obligatoria entre cualquier LLM y el cliente final: si
una respuesta menciona una norma o artículo que no existe (o estaba
derogado en el devengo), el guard la marca como `block` y la frena antes
de que llegue a producción.
"""

from .citation_guard import (
    Citation,
    CitationCheckResult,
    CitationIssue,
    CitationKind,
    IssueLevel,
    Verdict,
    extract_citations,
    verify_citations,
)

__all__ = [
    "Citation",
    "CitationCheckResult",
    "CitationIssue",
    "CitationKind",
    "IssueLevel",
    "Verdict",
    "extract_citations",
    "verify_citations",
]
