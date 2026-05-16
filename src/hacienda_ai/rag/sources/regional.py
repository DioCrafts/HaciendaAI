"""Verificador de enlaces a boletines autonómicos y forales.

Los boletines regionales (BOCM, DOGC, BOJA, etc.) no exponen una API
consolidada con texto pinpoint comparable a la del BOE estatal, por lo
que no podemos calcular un SHA-256 estable del articulado. Lo que sí
podemos detectar de forma fiable es **enlace roto**: 404, 5xx o
timeout sobre la URL canónica declarada en cada `Source.url`.

Esto basta para el cron diario: si la CCAA reubica el documento o lo
retira sin redirección, el corpus se entera al día siguiente y el
asesor humano valida si la cita sigue siendo válida.

Diseño:
- `check_url(url)` hace una petición HEAD (rápida) y, si la fuente no
  soporta HEAD, cae a GET con `Range: bytes=0-0` para no descargar el
  documento entero. Solo importa el `status_code`.
- `check_regional_urls(corpus, scales)` itera todas las fuentes con
  `boe_id` de un boletín reconocido y devuelve solo las URLs que han
  fallado (200/3xx no se reportan).

El cliente es deliberadamente sencillo: `urllib.request` para no añadir
una dependencia nueva por una llamada HTTP. Si en el futuro queremos
paralelizar o reintentar de forma inteligente, se sustituye este módulo
por uno basado en `httpx` sin tocar el resto del pipeline.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Iterable

from ...irpf.scales import TaxScale
from ...models import Deduction
from ..impact import BrokenRegionalURL

# Prefijos de los boletines autonómicos/forales reconocidos. Coincide con
# `models._common.REGIONAL_BULLETIN_PREFIXES`, replicado aquí para no
# crear una dependencia inversa entre el paquete `rag` y `models`.
REGIONAL_BULLETIN_PREFIXES = (
    "BOCM-",
    "DOGC-",
    "DOCV-",
    "BOJA-",
    "BOPV-",
    "BOB-",
    "BOG-",
    "BOTHA-",
    "BON-",
    "DOG-",
    "BOC-",
    "BORM-",
    "BOIB-",
    "BOCYL-",
    "DOCM-",
    "BOPA-",
    "DOE-",
    "BOR-",
    "BOA-",
)

USER_AGENT = "hacienda-ai-regional-check/0.1 (+https://github.com/DioCrafts/HaciendaAI)"
DEFAULT_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class URLCheckResult:
    """Resultado de comprobar una URL individual."""

    url: str
    ok: bool
    status_code: int | None
    error: str | None


def _is_regional(boe_id: str) -> bool:
    return any(boe_id.startswith(prefix) for prefix in REGIONAL_BULLETIN_PREFIXES)


def check_url(url: str, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> URLCheckResult:
    """Petición HEAD ligera. Si el servidor rechaza HEAD (405), cae a GET
    con Range mínimo. Cualquier 2xx/3xx se considera OK; 4xx/5xx fallan.

    No sigue redirecciones explícitamente: urllib lo hace por defecto
    para HEAD/GET sobre HTTPS, lo que también detectamos como "URL
    canónica vigente" — si la CCAA mueve el documento y devuelve 301,
    consideramos el enlace vivo.
    """
    req = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return URLCheckResult(url=url, ok=True, status_code=resp.status, error=None)
    except urllib.error.HTTPError as exc:
        # 405 Method Not Allowed: el servidor no acepta HEAD; fallback a GET.
        if exc.code == 405:
            return _check_url_with_get(url, timeout)
        return URLCheckResult(
            url=url, ok=False, status_code=exc.code, error=exc.reason
        )
    except urllib.error.URLError as exc:
        return URLCheckResult(url=url, ok=False, status_code=None, error=str(exc.reason))
    except (TimeoutError, OSError) as exc:
        return URLCheckResult(url=url, ok=False, status_code=None, error=str(exc))


def _check_url_with_get(url: str, timeout: float) -> URLCheckResult:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Range": "bytes=0-0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ok = 200 <= resp.status < 400
            return URLCheckResult(url=url, ok=ok, status_code=resp.status, error=None)
    except urllib.error.HTTPError as exc:
        return URLCheckResult(
            url=url, ok=False, status_code=exc.code, error=exc.reason
        )
    except urllib.error.URLError as exc:
        return URLCheckResult(url=url, ok=False, status_code=None, error=str(exc.reason))
    except (TimeoutError, OSError) as exc:
        return URLCheckResult(url=url, ok=False, status_code=None, error=str(exc))


def _iter_regional_sources(
    corpus: list[Deduction],
    scales: list[TaxScale],
) -> Iterable[tuple[str, str, str]]:
    """Yields `(carrier_id, boe_id_or_empty, url)` para cada fuente regional con URL.

    `carrier_id` identifica la entidad del corpus (deducción o escala) que
    declaró la fuente. Una misma URL puede salir varias veces si la citan
    varias deducciones; el caller deduplica luego.
    """
    for d in corpus:
        for src in d.sources:
            if not src.url:
                continue
            if src.boe_id and _is_regional(src.boe_id):
                yield (d.id, src.boe_id, src.url)
    for s in scales:
        for src in s.sources:
            if not src.url:
                continue
            if src.boe_id and _is_regional(src.boe_id):
                yield (s.id, src.boe_id, src.url)


def check_regional_urls(
    corpus: list[Deduction],
    scales: list[TaxScale],
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    checker: object | None = None,
) -> list[BrokenRegionalURL]:
    """Verifica todas las URLs regionales del corpus y devuelve las rotas.

    `checker` se inyecta solo en tests: una callable `(url, timeout) ->
    URLCheckResult`. Por defecto se usa `check_url` real.
    """
    check_fn = checker if checker is not None else check_url
    broken: list[BrokenRegionalURL] = []
    seen_urls: set[str] = set()
    for carrier_id, boe_id, url in _iter_regional_sources(corpus, scales):
        if url in seen_urls:
            continue
        seen_urls.add(url)
        result: URLCheckResult = check_fn(url, timeout)  # type: ignore[operator]
        if not result.ok:
            broken.append(
                BrokenRegionalURL(
                    url=url,
                    boe_id=boe_id or None,
                    deduction_id=carrier_id,
                    status_code=result.status_code,
                    error=result.error,
                )
            )
    return broken
