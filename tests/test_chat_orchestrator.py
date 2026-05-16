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


def test_orchestrator_guard_blocks_hallucinated_article(_resources, tools) -> None:
    """Si el modelo cierra con un artículo no documentado en el corpus, el
    guard sustituye el texto por SAFE_FALLBACK_MESSAGE y deja el original
    accesible en `blocked_text`."""
    script = [
        FakeTurn(text="Te aplico el art. 999 LIRPF: deducción especial de 500 €.")
    ]
    res = _run(script, _resources=_resources, tools=tools)
    assert res.citation_check.verdict == "block"
    assert res.blocked_text is not None
    assert "art. 999" in res.blocked_text
    assert res.assistant_text == SAFE_FALLBACK_MESSAGE
    # El historial conserva el bloque original + el mensaje seguro como
    # turno extra del asistente.
    assistant_turns = [m for m in res.history if m["role"] == "assistant"]
    assert len(assistant_turns) >= 2


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
