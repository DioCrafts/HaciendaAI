"""API HTTP mínima para demostración del motor IRPF.

Endpoints:

- `GET  /`                              — sirve la página estática de demo.
- `GET  /health`                        — sonda de vida.
- `GET  /deductions`                    — catálogo del corpus con pinpoint a BOE.
- `POST /profiles`                      — valida y guarda un `TaxProfile`.
- `GET  /profiles/{id}`                 — recupera un perfil guardado.
- `POST /evaluations`                   — evalúa el perfil y guarda el resultado.
- `GET  /evaluations/{id}`              — recupera una evaluación guardada.
- `GET  /evaluations/{id}/pdf`          — exporta la evaluación a PDF firmable.

Persistencia: SQLite local (`hacienda_ai.storage`), inyectada en
`create_app(db_path=...)`. Path por defecto: `~/.hacienda-ai/hacienda.db`,
sobreescribible vía env var `HACIENDA_AI_DB_PATH` o argumento explícito.
Los tests pasan `:memory:` para no tocar disco. Si se migra a Postgres,
basta con sustituir los dos repos del paquete `storage`.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from hacienda_ai import __version__
from hacienda_ai.api.pdf import render_evaluation_report_pdf
from hacienda_ai.chat import (
    SYSTEM_PROMPT,
    AnthropicClient,
    LegalContextRetriever,
    LLMClient,
    LLMUnavailable,
    build_default_registry,
    run_chat,
)
from hacienda_ai.deductions import load_deductions
from hacienda_ai.irpf import compute_quota, load_tax_scales
from hacienda_ai.irpf.quota import quota_to_dict
from hacienda_ai.models import (
    Deduction,
    NormaRegistry,
    Source,
    TaxProfile,
    ValidationError,
    is_state_bulletin_id,
)
from hacienda_ai.normas import load_norma_registry
from hacienda_ai.rules import evaluate_deductions
from hacienda_ai.safety import verify_citations
from hacienda_ai.storage import (
    ChatSessionsRepo,
    EvaluationsRepo,
    ProfilesRepo,
    init_db,
)

DISCLAIMER = (
    "Esta herramienta ofrece ayuda informativa. No sustituye a un asesor "
    "fiscal colegiado. Verifica las citas en BOE antes de cualquier "
    "presentación o recurso."
)
BOE_PINPOINT_URL = "https://www.boe.es/buscar/act.php?id={boe_id}#{anchor}"
STATIC_DIR = Path(__file__).resolve().parent / "static"

_ARTICLE_NUMERIC_RE = re.compile(r"art[íi]?\.?\s*(\d+)\s*(bis|ter|quater|quinquies)?")

# Etiquetas cualitativas de confianza expuestas en el API. Los valores
# numéricos de `RuleEvaluation.confidence` están fijados a mano por rama
# del motor (rules.py) y no están calibrados empíricamente; exponer un
# número con tres decimales sugiere una precisión que no tenemos. La
# etiqueta cualitativa transmite el nivel de la rama sin sobreprometer.
CONFIDENCE_THRESHOLDS = ((0.8, "alta"), (0.5, "media"))


def _qualitative_confidence(score: float) -> str:
    for threshold, label in CONFIDENCE_THRESHOLDS:
        if score >= threshold:
            return label
    return "baja"


def _anchor_from_article(article: str | None) -> str | None:
    if not article:
        return None
    if article.startswith("boe:"):
        return article.removeprefix("boe:")
    m = _ARTICLE_NUMERIC_RE.match(article.lower())
    if m:
        return f"a{m.group(1)}{m.group(2) or ''}"
    return None


def _source_payload(source: Source) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": source.kind.value,
        "title": source.title,
        "url": source.url,
        "article": source.article,
        "paragraph": source.paragraph,
        "boe_id": source.boe_id,
        "content_hash": source.content_hash,
        "checked_at": source.checked_at.isoformat() if source.checked_at else None,
    }
    anchor = _anchor_from_article(source.article)
    if source.boe_id and is_state_bulletin_id(source.boe_id) and anchor:
        base["pinpoint_url"] = BOE_PINPOINT_URL.format(boe_id=source.boe_id, anchor=anchor)
    return base


def _deduction_payload(deduction: Deduction) -> dict[str, Any]:
    return {
        "id": deduction.id,
        "name": deduction.name,
        "description": deduction.description,
        "tax_year": deduction.tax_year,
        "scope": deduction.scope.value,
        "region": deduction.region,
        "category": deduction.category.value,
        "risk_level": deduction.risk_level.value,
        "validation_status": deduction.validation_status.value,
        "effective_from": deduction.effective_from.isoformat() if deduction.effective_from else None,
        "effective_to": deduction.effective_to.isoformat() if deduction.effective_to else None,
        "last_reviewed_at": (
            deduction.last_reviewed_at.isoformat() if deduction.last_reviewed_at else None
        ),
        "sources": [_source_payload(s) for s in deduction.sources],
    }


def _applicable_versions(
    deduction: Deduction, registry: NormaRegistry, devengo: date
) -> list[dict[str, Any]]:
    """Versión vigente en `devengo` para cada norma única citada por la deducción.

    Deduplica por `boe_id` para evitar repetir la misma versión LIRPF cuando una
    deducción cita varios artículos del mismo texto consolidado.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in deduction.sources:
        if source.boe_id is None or source.boe_id in seen:
            continue
        if not registry.knows(source.boe_id):
            continue
        version = registry.version_at(source.boe_id, devengo)
        if version is None:
            continue
        seen.add(source.boe_id)
        out.append(
            {
                "boe_id": source.boe_id,
                "effective_from": version.effective_from.isoformat(),
                "effective_to": (
                    version.effective_to.isoformat() if version.effective_to else None
                ),
                "status": version.status.value,
                "modified_by_boe_id": version.modified_by_boe_id,
                "notes": version.notes,
            }
        )
    return out


def _canonical_default(value: Any) -> Any:
    """Serializa los tipos no-JSON que pueden aparecer dentro de `Deduction`.

    `asdict` deja los Enum como Enum y las fechas como `date`; los convertimos
    a sus formas estables (Enum.value, ISO 8601) para que el hash sea
    reproducible entre versiones de Python y plataformas.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"no serializable en fingerprint: {type(value).__name__}")


def _corpus_fingerprint(deductions: list[Deduction]) -> str:
    """SHA-256 de la serialización canónica completa del corpus.

    Firma la representación íntegra de cada `Deduction` —`requirements`,
    `calculation` (tipo, importe fijo, porcentaje, cap, base_field), `limit`,
    `validation_status`, `effective_from`/`effective_to`, `risk_level`,
    `incompatibilities`, `required_documents`, `rent_web_boxes`,
    `taxable_base_limits`, `foral_territory` y todas las `sources` con su
    `content_hash`—, no solo `(id, tax_year, sources)` como hacía la primera
    versión. Cualquier cambio semántico —incluido modificar un importe sin
    tocar las fuentes BOE, ajustar un tope o cambiar `validation_status`—
    mueve la firma, y el PDF firmado lo refleja.

    Determinista: ordenamos las deducciones por `id` y las `sources` de cada
    deducción por `(boe_id, article, paragraph, content_hash)` para que el
    orden de la lista de entrada y el orden de declaración de fuentes no
    afecten al hash.
    """
    canonical: list[dict[str, Any]] = []
    for d in sorted(deductions, key=lambda d: d.id):
        raw = asdict(d)
        raw["sources"] = sorted(
            raw["sources"],
            key=lambda s: (
                s.get("boe_id") or "",
                s.get("article") or "",
                s.get("paragraph") or "",
                s.get("content_hash") or "",
            ),
        )
        canonical.append(raw)
    payload = json.dumps(
        canonical,
        sort_keys=True,
        ensure_ascii=False,
        default=_canonical_default,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_app(
    db_path: str | Path | None = None,
    *,
    llm_client: LLMClient | None = None,
    retriever: LegalContextRetriever | None = None,
) -> FastAPI:
    """Construye una instancia nueva del app, útil para tests aislados.

    `db_path`:
    - `None` (defecto): se resuelve por env var `HACIENDA_AI_DB_PATH` y, si
      tampoco está, `~/.hacienda-ai/hacienda.db`.
    - Ruta de archivo: la DB persiste entre arranques (uso real).
    - `":memory:"`: DB en RAM, no sobrevive al proceso (uso en tests).

    `llm_client`: inyectable para tests (un `FakeLLMClient` con guion
    determinista). En producción se deja `None`: el endpoint /chat
    intentará crear un `AnthropicClient` real con la API key del
    entorno; si no está disponible, devuelve 503 con motivo.

    `retriever`: si se inyecta, activa el cableado RAG en /chat. La
    tool `retrieve_legal_context` queda disponible para el LLM y el
    orquestador hace pre-fetch antes del primer turno. Sin retriever
    (defecto actual), /chat sigue funcionando solo con las cinco
    tools deterministas — no hay regresión.
    """
    app = FastAPI(
        title="HaciendaAI — Demo API",
        version=__version__,
        description="API de demostración. No es producción ni asesoramiento profesional.",
    )

    conn = init_db(db_path)
    profiles_repo = ProfilesRepo(conn)
    evaluations_repo = EvaluationsRepo(conn)
    chat_repo = ChatSessionsRepo(conn)
    deductions = load_deductions()
    registry = load_norma_registry()
    tax_scales = load_tax_scales()
    chat_tools = build_default_registry(
        deductions=deductions,
        registry=registry,
        scales=tax_scales,
        retriever=retriever,
    )
    last_reviewed = max(
        (d.last_reviewed_at for d in deductions if d.last_reviewed_at),
        default=None,
    )
    corpus_meta: dict[str, Any] = {
        "count": len(deductions),
        "last_reviewed_at": last_reviewed.isoformat() if last_reviewed else None,
        "engine_version": __version__,
        "normas_registered": sum(1 for d in deductions for s in d.sources if s.boe_id and registry.knows(s.boe_id)) > 0,
        "fingerprint_sha256": _corpus_fingerprint(deductions),
    }

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/deductions")
    def list_deductions() -> dict[str, Any]:
        return {
            "corpus": corpus_meta,
            "disclaimer": DISCLAIMER,
            "deductions": [_deduction_payload(d) for d in deductions],
        }

    @app.post("/profiles", status_code=201)
    def create_profile(body: dict[str, Any]) -> dict[str, Any]:
        try:
            profile = TaxProfile.from_dict(body)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        pid = uuid.uuid4().hex
        profiles_repo.save(pid, profile)
        return {"profile_id": pid, "profile": profile.to_dict()}

    @app.get("/profiles/{profile_id}")
    def get_profile(profile_id: str) -> dict[str, Any]:
        profile = profiles_repo.get(profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile_id no encontrado")
        return {"profile_id": profile_id, "profile": profile.to_dict()}

    @app.post("/evaluations", status_code=201)
    def create_evaluation(body: dict[str, Any]) -> dict[str, Any]:
        pid = body.get("profile_id")
        if not isinstance(pid, str) or not pid:
            raise HTTPException(status_code=422, detail="profile_id (string) requerido")
        profile = profiles_repo.get(pid)
        if profile is None:
            raise HTTPException(status_code=404, detail="profile_id no encontrado")
        devengo = profile.effective_devengo_date()
        t0 = time.perf_counter()
        evaluations = evaluate_deductions(deductions, profile, registry)
        ded_by_id = {d.id: d for d in deductions}
        quota = compute_quota(profile, evaluations, ded_by_id, tax_scales, devengo)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        items: list[dict[str, Any]] = []
        for ev in evaluations:
            ded = ded_by_id.get(ev.deduction_id)
            items.append(
                {
                    "deduction_id": ev.deduction_id,
                    "deduction_name": ded.name if ded else ev.deduction_id,
                    "category": ded.category.value if ded else None,
                    "status": ev.status,
                    "estimated_amount": ev.estimated_amount,
                    "reason": ev.reason,
                    "missing_fields": list(ev.missing_fields),
                    "missing_documents": list(ev.missing_documents),
                    "risk_level": ev.risk_level,
                    "confidence": _qualitative_confidence(ev.confidence),
                    "sources": [_source_payload(s) for s in ev.sources],
                    "applicable_versions": (
                        _applicable_versions(ded, registry, devengo) if ded else []
                    ),
                }
            )

        evaluation_id = uuid.uuid4().hex
        response: dict[str, Any] = {
            "evaluation_id": evaluation_id,
            "profile_id": pid,
            "profile": profile.to_dict(),
            "devengo_date": devengo.isoformat(),
            "evaluated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "elapsed_ms": round(elapsed_ms, 2),
            "corpus": corpus_meta,
            "disclaimer": DISCLAIMER,
            "evaluations": items,
            "cuota": quota_to_dict(quota),
        }
        evaluations_repo.save(evaluation_id, pid, response)
        return response

    @app.get("/evaluations/{evaluation_id}")
    def get_evaluation(evaluation_id: str) -> dict[str, Any]:
        stored = evaluations_repo.get(evaluation_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="evaluation_id no encontrado")
        return stored

    @app.get("/evaluations/{evaluation_id}/pdf")
    def download_evaluation_pdf(evaluation_id: str) -> Response:
        stored = evaluations_repo.get(evaluation_id)
        if stored is None:
            raise HTTPException(status_code=404, detail="evaluation_id no encontrado")
        pdf_bytes = render_evaluation_report_pdf(stored)
        filename = f"hacienda-ai-evaluacion-{evaluation_id[:8]}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.post("/citations/verify")
    def verify_citations_endpoint(body: dict[str, Any]) -> dict[str, Any]:
        """Verifica las citas legales contenidas en un texto.

        Pensado como guard rail antes de devolver al cliente cualquier
        respuesta generada por un LLM: si el texto cita una norma o
        artículo que no existe (o estaba derogado en el devengo), el
        endpoint devuelve `verdict="block"` y el llamante debe rechazar
        la respuesta. Es independiente del flujo de evaluación: no
        requiere un perfil ni una evaluación previa.

        Body esperado:
            {"text": "...", "devengo_date": "2024-12-31" (opcional)}
        """
        text = body.get("text")
        if not isinstance(text, str):
            raise HTTPException(
                status_code=422, detail="text (string) requerido"
            )
        devengo_raw = body.get("devengo_date")
        devengo: date | None = None
        if isinstance(devengo_raw, str) and devengo_raw:
            try:
                devengo = date.fromisoformat(devengo_raw)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail="devengo_date debe ser ISO 8601 (YYYY-MM-DD)",
                ) from exc
        result = verify_citations(
            text,
            corpus=deductions,
            scales=tax_scales,
            registry=registry,
            devengo=devengo,
        )
        return {
            "verdict": result.verdict,
            "citations": [
                {
                    "kind": c.kind,
                    "raw": c.raw,
                    "span": list(c.span),
                    "boe_id": c.boe_id,
                    "article": c.article,
                    "article_suffix": c.article_suffix,
                    "paragraph": c.paragraph,
                    "law_label": c.law_label,
                    "juris_kind": c.juris_kind,
                    "juris_ref": c.juris_ref,
                }
                for c in result.citations
            ],
            "issues": [
                {
                    "level": i.level,
                    "code": i.code,
                    "message": i.message,
                    "citation_raw": i.citation.raw,
                    "citation_span": list(i.citation.span),
                }
                for i in result.issues
            ],
        }

    def _resolve_llm() -> LLMClient:
        if llm_client is not None:
            return llm_client
        try:
            return AnthropicClient()
        except LLMUnavailable as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Cliente LLM no disponible: {exc}. Configura "
                    "ANTHROPIC_API_KEY o inyecta un LLMClient en create_app()."
                ),
            ) from exc

    @app.post("/chat")
    def chat_endpoint(body: dict[str, Any]) -> dict[str, Any]:
        """Orquestador conversacional con tool use estricto y guard de citas.

        Body:
            {
              "message": "...",
              "session_id": "uuid"   (opcional: continuar conversación),
              "devengo_date": "YYYY-MM-DD"  (opcional)
            }

        Respuesta:
            {
              "session_id": "...",
              "assistant": "texto final ya verificado",
              "blocked_text": "texto que el guard rechazó (si hubo block)",
              "tool_invocations": [...],
              "citation_check": {verdict, blocking_issues, warnings},
              "iterations": N,
              "disclaimer": "..."
            }
        """
        message = body.get("message")
        if not isinstance(message, str) or not message.strip():
            raise HTTPException(status_code=422, detail="message (string) requerido")
        session_id_raw = body.get("session_id")
        if session_id_raw is not None and not isinstance(session_id_raw, str):
            raise HTTPException(status_code=422, detail="session_id debe ser string")
        session_id = session_id_raw or uuid.uuid4().hex
        devengo: date | None = None
        devengo_raw = body.get("devengo_date")
        if isinstance(devengo_raw, str) and devengo_raw:
            try:
                devengo = date.fromisoformat(devengo_raw)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail="devengo_date debe ser ISO 8601 (YYYY-MM-DD)",
                ) from exc

        history = chat_repo.get(session_id) or []
        llm = _resolve_llm()

        result = run_chat(
            user_message=message,
            history=history,
            llm=llm,
            tools=chat_tools,
            system_prompt=SYSTEM_PROMPT,
            devengo=devengo,
            corpus=deductions,
            registry=registry,
            scales=tax_scales,
            retriever=retriever,
        )
        chat_repo.save(session_id, result.history)

        return {
            "session_id": session_id,
            "assistant": result.assistant_text,
            "blocked_text": result.blocked_text,
            "tool_invocations": result.tool_invocations,
            "citation_check": result.to_dict()["citation_check"],
            "iterations": result.iterations,
            "stop_reason": result.stop_reason,
            "disclaimer": DISCLAIMER,
        }

    @app.get("/chat/sessions/{session_id}")
    def get_chat_session(session_id: str) -> dict[str, Any]:
        history = chat_repo.get(session_id)
        if history is None:
            raise HTTPException(
                status_code=404, detail="session_id no encontrada"
            )
        return {"session_id": session_id, "history": history}

    @app.get("/")
    def root() -> FileResponse:
        return FileResponse(STATIC_DIR / "demo.html")

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


app = create_app()
