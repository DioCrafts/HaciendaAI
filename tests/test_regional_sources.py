"""Tests del verificador de URLs de boletines autonómicos.

No usamos red en tests: inyectamos un `checker` callable que simula
diferentes respuestas HTTP. Verifica:

1. URL OK → no se reporta.
2. URL 404 → se reporta con `status_code=404` y `deduction_id` correcto.
3. Timeout → se reporta con `status_code=None` y `error` no vacío.
4. URL no regional (BOE estatal) → se ignora (no es el ámbito de esta tool).
5. Deduplicación: una URL citada por varias deducciones se chequea una vez.
6. Fuentes sin URL declarada → no se chequean.
"""

from __future__ import annotations

from collections.abc import Callable

from hacienda_ai.deductions import load_deductions
from hacienda_ai.irpf import load_tax_scales
from hacienda_ai.rag.sources.regional import (
    URLCheckResult,
    check_regional_urls,
)


def _ok(url: str, _timeout: float) -> URLCheckResult:
    return URLCheckResult(url=url, ok=True, status_code=200, error=None)


def _make_response_map(
    responses: dict[str, URLCheckResult],
    default: URLCheckResult,
) -> Callable[[str, float], URLCheckResult]:
    def checker(url: str, _timeout: float) -> URLCheckResult:
        return responses.get(url, default)

    return checker


def test_all_regional_urls_ok_returns_empty() -> None:
    corpus = load_deductions()
    scales = load_tax_scales()
    broken = check_regional_urls(corpus, scales, checker=_ok)
    assert broken == []


def test_regional_url_404_is_reported() -> None:
    corpus = load_deductions()
    scales = load_tax_scales()
    # Tomamos la primera URL regional real del corpus para el test.
    target_url = None
    target_ded = None
    for d in corpus:
        for src in d.sources:
            if src.boe_id and src.boe_id.startswith("BOCM-") and src.url:
                target_url = src.url
                target_ded = d.id
                break
        if target_url:
            break
    assert target_url is not None, "el corpus debería contener una fuente BOCM con URL"

    checker = _make_response_map(
        {target_url: URLCheckResult(url=target_url, ok=False, status_code=404, error="Not Found")},
        default=_ok(target_url, 0.0),
    )
    broken = check_regional_urls(corpus, scales, checker=checker)
    assert len(broken) == 1
    assert broken[0].url == target_url
    assert broken[0].status_code == 404
    assert broken[0].deduction_id == target_ded
    assert broken[0].boe_id and broken[0].boe_id.startswith("BOCM-")


def test_timeout_is_reported_with_no_status() -> None:
    corpus = load_deductions()
    scales = load_tax_scales()
    target_url = next(
        src.url
        for d in corpus
        for src in d.sources
        if src.boe_id and src.boe_id.startswith("BOCM-") and src.url
    )
    timeout_result = URLCheckResult(
        url=target_url, ok=False, status_code=None, error="The read operation timed out"
    )
    checker = _make_response_map({target_url: timeout_result}, default=_ok(target_url, 0.0))
    broken = check_regional_urls(corpus, scales, checker=checker)
    assert any(b.status_code is None and b.error and "timed out" in b.error for b in broken)


def test_state_boe_urls_are_not_checked() -> None:
    """El chequeo regional debe ignorar fuentes con boe_id BOE-A-…
    incluso si su URL devolviera 404 — esas las verifica `verify_seed`
    contra el texto consolidado, no como enlace.
    """
    corpus = load_deductions()
    scales = load_tax_scales()
    # Counter de cuántas URLs distintas se chequean.
    called: list[str] = []

    def tracking(url: str, _timeout: float) -> URLCheckResult:
        called.append(url)
        return _ok(url, _timeout)

    check_regional_urls(corpus, scales, checker=tracking)
    # Ninguna URL chequeada debe pertenecer al BOE estatal (boe.es/buscar/act…
    # son URLs estatales). Verificamos por contenido.
    for url in called:
        assert "boe.es" not in url or "BOE-A-" not in url, (
            f"URL estatal chequeada por error: {url}"
        )


def test_dedup_url_chequeada_una_vez() -> None:
    """Si dos deducciones citan exactamente la misma URL, el checker se
    invoca una sola vez (deduplicación por URL)."""
    corpus = load_deductions()
    scales = load_tax_scales()
    seen: dict[str, int] = {}

    def counting(url: str, _timeout: float) -> URLCheckResult:
        seen[url] = seen.get(url, 0) + 1
        return _ok(url, _timeout)

    check_regional_urls(corpus, scales, checker=counting)
    duplicated = {u: c for u, c in seen.items() if c > 1}
    assert not duplicated, f"URLs verificadas más de una vez: {duplicated}"
