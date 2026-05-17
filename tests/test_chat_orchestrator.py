"""Tests del orquestador conversacional con FakeLLM.

Cubre:

1. Loop tool_use: el LLM pide una tool → se ejecuta localmente → resultado
   se reinyecta como `tool_result` → el LLM cierra con texto.
2. Guard rail final: una respuesta con `art. 999 LIRPF` se bloquea y se
   sustituye por `SAFE_FALLBACK_MESSAGE`. El texto original queda
   accesible en `result.blocked_text` para auditoría.
3. Múltiples tools encadenadas: search_norma → evaluate_profile →
   compute_irpf_quota.
4. Comportamiento ante max_iterations agotadas.
5. Historial: el resultado conserva los bloques de tool_use y tool_result
   para que el frontend pueda mostrar la traza.
"""

from __future__ import annotations

from datetime import date

import pytest

from hacienda_ai.chat import (
    MAX_VERIFY_RETRIES,
    RAG_CONTEXT_INTRO,
    RAG_CONTEXT_OUTRO,
    SAFE_FALLBACK_MESSAGE,
    SYSTEM_PROMPT,
    FakeLLMClient,
    FakeTurn,
    build_default_registry,
    run_chat,
)
from hacienda_ai.deductions import load_deductions
from hacienda_ai.irpf import load_tax_scales
from hacienda_ai.normas import load_norma_registry
from hacienda_ai.rag.vector import (
    EmbeddedChunk,
    SourceType,
    VectorMatch,
    VectorQuery,
)


@pytest.fixture(scope="module")
def _resources():
    corpus = load_deductions()
    registry = load_norma_registry()
    scales = load_tax_scales()
    return corpus, registry, scales


@pytest.fixture
def tools(_resources):
    corpus, registry, scales = _resources
    return build_default_registry(deductions=corpus, registry=registry, scales=scales)


def _run(script, message="Hola", _resources=None, tools=None, devengo=date(2024, 12, 31)):
    corpus, registry, scales = _resources
    fake = FakeLLMClient(script)
    return run_chat(
        user_message=message,
        history=None,
        llm=fake,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        devengo=devengo,
        corpus=corpus,
        registry=registry,
        scales=scales,
    )


def test_orchestrator_returns_text_when_no_tools_called(_resources, tools) -> None:
    script = [FakeTurn(text="Necesito que me digas tu CCAA antes de calcular.")]
    res = _run(script, _resources=_resources, tools=tools)
    assert res.iterations == 1
    assert res.tool_invocations == []
    assert "CCAA" in res.assistant_text
    assert res.citation_check.verdict == "safe"


def test_orchestrator_executes_tool_and_closes(_resources, tools) -> None:
    """Loop completo: LLM pide compute_irpf_quota → resultado → texto final."""
    script = [
        FakeTurn(
            text="Voy a calcular.",
            tool_calls=[
                (
                    "compute_irpf_quota",
                    {
                        "profile": {
                            "tax_year": 2024,
                            "region": "Madrid",
                            "filing_mode": "individual",
                            "personal": {"has_disability": False},
                            "family": {"children_count": 1, "ascendants_count": 0},
                            "income": {"work_gross": 30000, "work_net": 27500},
                            "expenses": {},
                            "documents": [
                                "Libro de familia o certificado de convivencia"
                            ],
                        }
                    },
                )
            ],
        ),
        FakeTurn(
            text=(
                "Tu cuota íntegra estatal es 2.452,50 € según el art. 63 LIRPF. "
                "Este análisis no sustituye a un asesor fiscal colegiado."
            )
        ),
    ]
    res = _run(script, _resources=_resources, tools=tools)
    assert res.iterations == 2
    assert len(res.tool_invocations) == 1
    assert res.tool_invocations[0]["tool"] == "compute_irpf_quota"
    assert res.citation_check.verdict == "safe"
    assert "2.452,50" in res.assistant_text


def test_orchestrator_falls_back_after_exhausting_verify_retries(
    _resources, tools
) -> None:
    """Modelo persistente: insiste en una cita alucinada incluso después del
    feedback. Tras agotar los reintentos, el guard sustituye el texto por
    SAFE_FALLBACK_MESSAGE, deja el último intento en `blocked_text` y
    `verify_history` queda con N+1 entradas en `block`."""
    bad_text = "Te aplico el art. 999 LIRPF: deducción especial de 500 €."
    script = [FakeTurn(text=bad_text) for _ in range(MAX_VERIFY_RETRIES + 1)]
    res = _run(script, _resources=_resources, tools=tools)

    assert res.citation_check.verdict == "block"
    assert res.blocked_text is not None
    assert "art. 999" in res.blocked_text
    assert res.assistant_text == SAFE_FALLBACK_MESSAGE
    assert res.verify_attempts == MAX_VERIFY_RETRIES + 1
    assert all(c.verdict == "block" for c in res.verify_history)

    # El historial recoge tantos turnos assistant (texto problemático)
    # como intentos hechos + el assistant final con el SAFE_FALLBACK.
    assistant_turns = [m for m in res.history if m["role"] == "assistant"]
    assert len(assistant_turns) == MAX_VERIFY_RETRIES + 2

    # Entre cada intento problemático y el siguiente hay un mensaje
    # `user` con el feedback estructurado (lista de issues).
    feedback_user_msgs = [
        m
        for m in res.history
        if m["role"] == "user"
        and isinstance(m.get("content"), str)
        and "verificador de citas" in m["content"]
    ]
    assert len(feedback_user_msgs) == MAX_VERIFY_RETRIES
    # El feedback incluye el código del issue concreto.
    assert any(
        "ARTICLE_NOT_IN_CORPUS" in m["content"] for m in feedback_user_msgs
    )


def test_orchestrator_recovers_when_model_reformulates_after_feedback(
    _resources, tools
) -> None:
    """Modelo que aprende del feedback: primer intento alucina, segundo
    intento corrige. El resultado final es safe y `blocked_text` queda
    None (no se aplicó el fallback)."""
    script = [
        FakeTurn(text="Aplica el art. 999 LIRPF: deducción mágica."),
        FakeTurn(
            text=(
                "Disculpa, mi cita no era correcta. No identifico una "
                "deducción aplicable en el corpus auditable para tu caso."
            )
        ),
    ]
    res = _run(script, _resources=_resources, tools=tools)

    assert res.citation_check.verdict == "safe"
    assert res.blocked_text is None
    assert res.assistant_text.startswith("Disculpa")
    assert res.verify_attempts == 2
    assert [c.verdict for c in res.verify_history] == ["block", "safe"]


def test_orchestrator_legacy_mode_falls_back_immediately(_resources, tools) -> None:
    """Con `max_verify_retries=0` se reproduce el comportamiento legacy:
    el primer block dispara el fallback sin reintentos."""
    corpus, registry, scales = _resources
    fake = FakeLLMClient(
        [FakeTurn(text="Te aplico el art. 999 LIRPF: deducción de 500 €.")]
    )
    res = run_chat(
        user_message="Hola",
        history=None,
        llm=fake,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        devengo=date(2024, 12, 31),
        max_verify_retries=0,
        corpus=corpus,
        registry=registry,
        scales=scales,
    )
    assert res.assistant_text == SAFE_FALLBACK_MESSAGE
    assert res.blocked_text is not None
    assert "art. 999" in res.blocked_text
    assert res.verify_attempts == 1


def test_orchestrator_safe_first_attempt_does_not_trigger_retry(
    _resources, tools
) -> None:
    """Respuesta limpia al primer intento: `verify_attempts=1`, sin
    feedback en el historial, sin entradas extra de assistant."""
    script = [FakeTurn(text="Necesito tu CCAA antes de calcular.")]
    res = _run(script, _resources=_resources, tools=tools)
    assert res.citation_check.verdict == "safe"
    assert res.verify_attempts == 1
    feedback_msgs = [
        m
        for m in res.history
        if m["role"] == "user"
        and isinstance(m.get("content"), str)
        and "verificador de citas" in m["content"]
    ]
    assert feedback_msgs == []


def test_orchestrator_warn_verdict_does_not_trigger_retry(
    _resources, tools
) -> None:
    """`warn` (p.ej. jurisprudencia no indexada) se acepta sin reintentar:
    solo los `block` dispararan retry."""
    # STS no indexado dispara WARN, no BLOCK.
    script = [
        FakeTurn(text="Según STS 1234/2024, el criterio es claro.")
    ]
    res = _run(script, _resources=_resources, tools=tools)
    assert res.citation_check.verdict == "warn"
    assert res.verify_attempts == 1
    assert res.blocked_text is None
    assert res.assistant_text.startswith("Según STS")


def test_orchestrator_negative_max_verify_retries_raises(_resources, tools) -> None:
    corpus, registry, scales = _resources
    fake = FakeLLMClient([FakeTurn(text="x")])
    with pytest.raises(ValueError, match="max_verify_retries"):
        run_chat(
            user_message="Hola",
            history=None,
            llm=fake,
            tools=tools,
            system_prompt=SYSTEM_PROMPT,
            max_verify_retries=-1,
            corpus=corpus,
            registry=registry,
            scales=scales,
        )


def test_orchestrator_chained_tool_calls(_resources, tools) -> None:
    """search_norma → evaluate_profile → compute_irpf_quota → texto final."""
    profile_dict = {
        "tax_year": 2024,
        "region": "Madrid",
        "filing_mode": "individual",
        "personal": {"has_disability": False},
        "family": {"children_count": 0, "ascendants_count": 0},
        "income": {"work_gross": 30000, "work_net": 27500},
        "expenses": {},
        "documents": [],
    }
    script = [
        FakeTurn(tool_calls=[("search_norma", {"query": "minimo contribuyente"})]),
        FakeTurn(tool_calls=[("evaluate_profile", {"profile": profile_dict})]),
        FakeTurn(tool_calls=[("compute_irpf_quota", {"profile": profile_dict})]),
        FakeTurn(text="Resumen: art. 57 LIRPF aplica mínimo de 5.550 €."),
    ]
    res = _run(script, _resources=_resources, tools=tools)
    assert [ti["tool"] for ti in res.tool_invocations] == [
        "search_norma",
        "evaluate_profile",
        "compute_irpf_quota",
    ]
    assert res.iterations == 4
    assert res.citation_check.verdict == "safe"


def test_orchestrator_invalid_tool_input_is_handled_not_crashed(
    _resources, tools
) -> None:
    """Si el LLM pasa un profile inválido, la tool devuelve `error` y el
    LLM puede reformular. El orquestador no debe crashear."""
    script = [
        FakeTurn(tool_calls=[("evaluate_profile", {"profile": "bogus"})]),
        FakeTurn(text="Necesito un perfil bien estructurado."),
    ]
    res = _run(script, _resources=_resources, tools=tools)
    assert res.assistant_text.startswith("Necesito")
    # La traza guarda la llamada fallida.
    assert res.tool_invocations[0]["tool"] == "evaluate_profile"


def test_orchestrator_respects_max_iterations(_resources, tools) -> None:
    # Guion infinito: LLM pide siempre la misma tool.
    looper = [
        FakeTurn(tool_calls=[("search_norma", {"query": "ley"})])
        for _ in range(20)
    ]
    corpus, registry, scales = _resources
    fake = FakeLLMClient(looper)
    res = run_chat(
        user_message="Hola",
        history=None,
        llm=fake,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        max_iterations=3,
        corpus=corpus,
        registry=registry,
        scales=scales,
    )
    assert res.iterations == 3
    assert "límite de iteraciones" in res.assistant_text


def test_orchestrator_history_records_tool_results(_resources, tools) -> None:
    script = [
        FakeTurn(tool_calls=[("search_norma", {"query": "minimo"})]),
        FakeTurn(text="Listo."),
    ]
    res = _run(script, _resources=_resources, tools=tools)
    # Una llamada al LLM con tool_use produce: assistant(tool_use) + user(tool_result)
    user_with_tool_result = [
        m
        for m in res.history
        if m["role"] == "user"
        and isinstance(m["content"], list)
        and any(b.get("type") == "tool_result" for b in m["content"])
    ]
    assert user_with_tool_result, "el historial no contiene tool_result"


class _StubRetriever:
    """Retriever fake que devuelve una lista predefinida de matches.

    Registra todas las queries recibidas para que los tests puedan
    aserver sobre el VectorQuery construido por el orquestador.
    """

    def __init__(self, matches: list[VectorMatch]) -> None:
        self._matches = list(matches)
        self.calls: list[VectorQuery] = []

    def search(self, query: VectorQuery) -> list[VectorMatch]:
        self.calls.append(query)
        return list(self._matches)


def _stub_norma_match(
    chunk_id: str = "norma::BOE-A-2006-20764::art-19",
    text: str = (
        "Articulo 19. Rendimientos netos del trabajo. Los gastos de defensa "
        "juridica derivados directamente de litigios suscitados en la "
        "relacion del contribuyente con la persona de la que percibe los "
        "rendimientos son deducibles con el limite de 300 euros anuales."
    ),
) -> VectorMatch:
    chunk = EmbeddedChunk(
        chunk_id=chunk_id,
        source_type=SourceType.NORMA,
        text=text,
        embedding=(0.0,),
        embedding_model="stub",
        metadata={
            "boe_id": "BOE-A-2006-20764",
            "articulo": "art. 19",
            "apartado": "2.e)",
            "impuesto": "irpf",
            "effective_from": "2015-01-01",
        },
    )
    return VectorMatch(chunk=chunk, score=0.91)


def test_orchestrator_injects_rag_context_into_system(_resources, tools) -> None:
    """Con un retriever inyectado, el system del primer turno debe contener
    el bloque [FUENTE 1] y los chunk_ids deben quedar en el ChatResult."""
    corpus, registry, scales = _resources
    retriever = _StubRetriever([_stub_norma_match()])
    fake = FakeLLMClient(
        [FakeTurn(text="Sí, son deducibles con el límite del art. 19 LIRPF.")]
    )
    res = run_chat(
        user_message="¿Son deducibles los gastos de defensa jurídica?",
        history=None,
        llm=fake,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        devengo=date(2024, 12, 31),
        impuesto="irpf",
        retriever=retriever,
        corpus=corpus,
        registry=registry,
        scales=scales,
    )

    # 1. El retriever fue invocado con la query del usuario, devengo e impuesto.
    assert len(retriever.calls) == 1
    query = retriever.calls[0]
    assert query.text.startswith("¿Son deducibles")
    assert query.fecha_devengo == date(2024, 12, 31)
    assert query.impuesto == "irpf"

    # 2. El system enviado al LLM se extendió con el contexto RAG.
    seen_system = fake.calls[0]["system"]
    assert SYSTEM_PROMPT in seen_system  # base intacto
    assert RAG_CONTEXT_INTRO in seen_system
    assert RAG_CONTEXT_OUTRO in seen_system
    assert "[FUENTE 1]" in seen_system
    assert "BOE-A-2006-20764" in seen_system

    # 3. Los ids recuperados se exponen en el resultado para auditoría.
    assert res.retrieved_chunk_ids == ["norma::BOE-A-2006-20764::art-19"]


def test_orchestrator_no_retriever_keeps_system_unchanged(_resources, tools) -> None:
    """Sin retriever (default), el system queda intacto y no se exponen
    chunk_ids — no debe haber regresión sobre el flujo previo a Fase 1."""
    corpus, registry, scales = _resources
    fake = FakeLLMClient([FakeTurn(text="Listo.")])
    res = run_chat(
        user_message="Hola",
        history=None,
        llm=fake,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        devengo=date(2024, 12, 31),
        corpus=corpus,
        registry=registry,
        scales=scales,
    )
    assert fake.calls[0]["system"] == SYSTEM_PROMPT
    assert res.retrieved_chunk_ids == []


def test_orchestrator_empty_retriever_does_not_inject_context(
    _resources, tools
) -> None:
    """Retriever que devuelve [] no debe inyectar contexto vacío al system."""
    corpus, registry, scales = _resources
    retriever = _StubRetriever([])
    fake = FakeLLMClient([FakeTurn(text="Listo.")])
    res = run_chat(
        user_message="Pregunta esotérica",
        history=None,
        llm=fake,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        retriever=retriever,
        corpus=corpus,
        registry=registry,
        scales=scales,
    )
    assert RAG_CONTEXT_INTRO not in fake.calls[0]["system"]
    assert fake.calls[0]["system"] == SYSTEM_PROMPT
    assert res.retrieved_chunk_ids == []


def test_orchestrator_retriever_failure_is_graceful(_resources, tools) -> None:
    """Si el retriever lanza (red caída, Qdrant inalcanzable, etc.) el chat
    continúa sin contexto: el RAG es complementario, no bloqueante."""

    class _Boom:
        def search(self, query: VectorQuery) -> list[VectorMatch]:
            raise RuntimeError("Qdrant unreachable")

    corpus, registry, scales = _resources
    fake = FakeLLMClient([FakeTurn(text="Sigo sin contexto.")])
    res = run_chat(
        user_message="Pregunta",
        history=None,
        llm=fake,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        retriever=_Boom(),
        corpus=corpus,
        registry=registry,
        scales=scales,
    )
    assert res.assistant_text == "Sigo sin contexto."
    assert res.retrieved_chunk_ids == []
    assert fake.calls[0]["system"] == SYSTEM_PROMPT


def test_orchestrator_rag_context_survives_tool_loop(_resources, tools) -> None:
    """El system extendido con RAG debe persistir en TODAS las iteraciones
    del loop (no solo en la primera). Es importante porque la respuesta
    final del LLM puede llegar tras varios tool_use → tool_result."""
    corpus, registry, scales = _resources
    retriever = _StubRetriever([_stub_norma_match()])
    profile = {
        "tax_year": 2024,
        "region": "Madrid",
        "filing_mode": "individual",
        "personal": {"has_disability": False},
        "family": {"children_count": 0, "ascendants_count": 0},
        "income": {"work_gross": 30000, "work_net": 27500},
        "expenses": {},
        "documents": [],
    }
    fake = FakeLLMClient(
        [
            FakeTurn(tool_calls=[("evaluate_profile", {"profile": profile})]),
            FakeTurn(text="Conclusión basada en [FUENTE 1]."),
        ]
    )
    run_chat(
        user_message="Calcula mi cuota.",
        history=None,
        llm=fake,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        devengo=date(2024, 12, 31),
        impuesto="irpf",
        retriever=retriever,
        corpus=corpus,
        registry=registry,
        scales=scales,
    )
    # Una sola llamada al retriever (antes del primer turno).
    assert len(retriever.calls) == 1
    # Todas las llamadas al LLM (en este caso 2) ven el system con RAG.
    for call in fake.calls:
        assert RAG_CONTEXT_INTRO in call["system"]


def test_orchestrator_blocks_when_llm_cites_unknown_ecli(
    _resources, tools
) -> None:
    """El orquestador, con `jurisprudence_registry` inyectado, debe bloquear
    una respuesta que cite un ECLI canónico ausente del corpus auditable."""
    from hacienda_ai.safety import JurisprudenceRegistry

    corpus, registry, scales = _resources
    # Añadimos UN ítem al registry para que `bool(reg)` sea True y se
    # aplique la política estricta. Cualquier ECLI distinto a ese se bloquea.
    from datetime import date as _date

    from hacienda_ai.models import (
        FalloSentido,
        Organo,
        RatioConfidence,
        Sentencia,
    )

    known = Sentencia(
        ecli="ECLI:ES:TS:2024:0001",
        organo=Organo.TS,
        tribunal_codigo="TS",
        sala=None,
        seccion=None,
        fecha=_date(2024, 1, 1),
        ponente=None,
        numero_resolucion=None,
        numero_recurso=None,
        fallo_sentido=FalloSentido.DESCONOCIDO,
        fallo_texto=".",
        ratio_decidendi=None,
        ratio_confidence=RatioConfidence.AUTO,
        resumen=None,
        url=None,
        content_hash="a" * 64,
        last_fetched_at=_date(2024, 1, 1),
    )
    juris_reg = JurisprudenceRegistry.from_items(sentencias=[known])

    # El LLM cita un ECLI inventado en su respuesta final. El guard
    # debería bloquear; con max_verify_retries=0 cae al fallback al
    # primer block.
    fake = FakeLLMClient(
        [
            FakeTurn(text="Según ECLI:ES:TS:2099:9999, la conclusión es X.")
        ]
    )
    res = run_chat(
        user_message="¿Qué dice la jurisprudencia?",
        history=None,
        llm=fake,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        devengo=date(2024, 12, 31),
        corpus=corpus,
        registry=registry,
        scales=scales,
        max_verify_retries=0,
        jurisprudence_registry=juris_reg,
    )
    assert res.assistant_text == SAFE_FALLBACK_MESSAGE
    assert res.blocked_text == "Según ECLI:ES:TS:2099:9999, la conclusión es X."
    assert res.citation_check.verdict == "block"
    assert any(
        i.code == "ECLI_NOT_IN_CORPUS"
        for i in res.citation_check.blocking_issues
    )


def test_orchestrator_allows_known_ecli(_resources, tools) -> None:
    """ECLI presente en el corpus → pasa al usuario sin bloqueo."""
    from datetime import date as _date

    from hacienda_ai.models import (
        FalloSentido,
        Organo,
        RatioConfidence,
        Sentencia,
    )
    from hacienda_ai.safety import JurisprudenceRegistry

    corpus, registry, scales = _resources
    senten = Sentencia(
        ecli="ECLI:ES:TS:2024:1234",
        organo=Organo.TS,
        tribunal_codigo="TS",
        sala=None,
        seccion=None,
        fecha=_date(2024, 6, 15),
        ponente=None,
        numero_resolucion=None,
        numero_recurso=None,
        fallo_sentido=FalloSentido.DESESTIMATORIA,
        fallo_texto=".",
        ratio_decidendi=None,
        ratio_confidence=RatioConfidence.AUTO,
        resumen=None,
        url=None,
        content_hash="a" * 64,
        last_fetched_at=_date(2024, 9, 1),
    )
    juris_reg = JurisprudenceRegistry.from_items(sentencias=[senten])
    fake = FakeLLMClient(
        [FakeTurn(text="Como dijo ECLI:ES:TS:2024:1234, la respuesta es así.")]
    )
    res = run_chat(
        user_message="¿Y la STS?",
        history=None,
        llm=fake,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        devengo=date(2024, 12, 31),
        corpus=corpus,
        registry=registry,
        scales=scales,
        jurisprudence_registry=juris_reg,
    )
    assert res.citation_check.verdict == "safe"
    assert res.blocked_text is None
    assert "ECLI:ES:TS:2024:1234" in res.assistant_text


def test_orchestrator_preserves_initial_history(_resources, tools) -> None:
    corpus, registry, scales = _resources
    prev_history = [
        {"role": "user", "content": "Hola, ¿quién eres?"},
        {"role": "assistant", "content": [{"type": "text", "text": "Soy un asistente."}]},
    ]
    fake = FakeLLMClient([FakeTurn(text="Continúo donde lo dejamos.")])
    res = run_chat(
        user_message="Sigue.",
        history=prev_history,
        llm=fake,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        corpus=corpus,
        registry=registry,
        scales=scales,
    )
    # Las dos primeras entradas del historial deben mantenerse intactas.
    assert res.history[0] == prev_history[0]
    assert res.history[1] == prev_history[1]
