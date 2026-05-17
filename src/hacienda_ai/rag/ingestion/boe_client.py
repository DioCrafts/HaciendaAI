"""Cliente HTTP del BOE con cache local, reintentos y rate-limit.

Encapsula las dos llamadas que necesitamos para la ingesta diaria:

- `fetch_summary(target_date)`: descarga el sumario del día.
- `fetch_document_xml(boe_id)`: descarga el XML de un documento publicado.

El cliente es deliberadamente simple (urllib, sin dependencias nuevas)
porque la carga es ligera: un sumario al día, ~10-30 documentos fiscales
por sumario. Si en el futuro paralelizamos o ingestamos histórico masivo,
se sustituye por `httpx` async sin tocar el resto del pipeline.

Decisiones:

- **Cache de disco**: cada respuesta se persiste en `cache_dir`. Si el
  fichero existe, se sirve desde caché. Esto permite que el cron sea
  idempotente: si ayer descargamos algo, hoy no volvemos a pegar al BOE.
  El cron limpia caché >30 días por fuera (no es responsabilidad de este
  módulo).

- **Reintentos**: 3 intentos con backoff exponencial (1s, 2s, 4s) ante
  errores transitorios de red (timeout, 5xx, conexión rota). 4xx no se
  reintenta (un 404 es definitivo: domingos, festivos, fecha inválida).

- **Rate-limit**: pausa de 200ms entre peticiones reales (no afecta a
  hits de caché). El BOE no publica límite oficial; este margen está
  alineado con `verify_seed.py` y no ha provocado throttling.

- **404 explícito**: lo levantamos como `BoeNotFoundError` para que el
  caller distinga "no hay BOE ese día" (esperado domingos) de "error
  real".
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from datetime import date
from http.client import HTTPResponse
from pathlib import Path
from typing import Callable, Protocol

USER_AGENT = "hacienda-ai-ingest-boe/0.1 (+https://github.com/DioCrafts/HaciendaAI)"

SUMMARY_URL = "https://www.boe.es/datosabiertos/api/boe/sumario/{yyyymmdd}"
DOCUMENT_XML_URL = "https://www.boe.es/diario_boe/xml.php?id={boe_id}"

DEFAULT_TIMEOUT = 30.0
DEFAULT_RATE_LIMIT_SECONDS = 0.2
DEFAULT_MAX_RETRIES = 3


class BoeFetchError(RuntimeError):
    """Error de red, parsing o respuesta inesperada del BOE."""


class BoeNotFoundError(BoeFetchError):
    """404 explícito: no existe sumario/documento para esa clave.

    Se separa para que el caller distinga el caso esperado (domingo sin BOE,
    `boe_id` mal escrito) del error transitorio que sí merece warning.
    """


class _Opener(Protocol):
    """Interfaz mínima del opener HTTP para inyección en tests."""

    def open(self, req: urllib.request.Request, timeout: float) -> HTTPResponse: ...


class BoeClient:
    """Cliente HTTP del BOE.

    `cache_dir` se crea bajo demanda. `clock` y `sleeper` permiten
    determinismo en tests (sin esperas reales, sin red).
    """

    def __init__(
        self,
        *,
        cache_dir: Path,
        timeout: float = DEFAULT_TIMEOUT,
        rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        opener: _Opener | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.rate_limit_seconds = rate_limit_seconds
        self.max_retries = max_retries
        # `opener` por defecto: módulo `urllib.request` (que expone `.urlopen`
        # con la firma esperada). Lo envolvemos para uniformar `.open()`.
        self._opener: _Opener | None = opener
        # `sleeper(seconds: float) -> None`. Por defecto `time.sleep`.
        self._sleep: Callable[[float], None] = (
            sleeper if sleeper is not None else time.sleep
        )

    # ---------- API pública ----------

    def fetch_summary(self, target: date) -> tuple[str, str]:
        """Devuelve `(payload, content_type)` del sumario de `target`.

        Si la respuesta es 404, levanta `BoeNotFoundError` — el caller decide
        si lo trata como "domingo sin BOE" o como fallo.
        """
        yyyymmdd = target.strftime("%Y%m%d")
        url = SUMMARY_URL.format(yyyymmdd=yyyymmdd)
        cache_key = self.cache_dir / "summaries" / f"{yyyymmdd}.json"
        return self._get_with_cache(
            url,
            cache_key,
            accept="application/json",
            content_type="application/json",
        )

    def fetch_document_xml(self, boe_id: str) -> str:
        """Devuelve el XML del documento publicado con identificador `boe_id`.

        Es el XML original del día de publicación, no el consolidado. Para
        el consolidado usar `scripts/verify_seed.py:fetch_consolidated`.
        """
        if not boe_id or not boe_id.startswith("BOE-"):
            raise BoeFetchError(f"boe_id inválido: {boe_id!r}")
        url = DOCUMENT_XML_URL.format(boe_id=boe_id)
        cache_key = self.cache_dir / "documents" / f"{boe_id}.xml"
        payload, _ = self._get_with_cache(
            url,
            cache_key,
            accept="application/xml",
            content_type="application/xml",
        )
        return payload

    # ---------- Internals ----------

    def _get_with_cache(
        self,
        url: str,
        cache_key: Path,
        *,
        accept: str,
        content_type: str,
    ) -> tuple[str, str]:
        if cache_key.exists():
            return cache_key.read_text(encoding="utf-8"), content_type
        payload = self._get_with_retry(url, accept=accept)
        cache_key.parent.mkdir(parents=True, exist_ok=True)
        cache_key.write_text(payload, encoding="utf-8")
        # Rate-limit solo tras peticiones reales (cache hits son gratis).
        self._sleep(self.rate_limit_seconds)
        return payload, content_type

    def _get_with_retry(self, url: str, *, accept: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._raw_get(url, accept=accept)
            except BoeNotFoundError:
                # 404 es definitivo, no reintentar.
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                # Backoff exponencial: 1s, 2s, 4s...
                self._sleep(2**attempt)
        raise BoeFetchError(
            f"GET {url} falló tras {self.max_retries} intentos: {last_error}"
        ) from last_error

    def _raw_get(self, url: str, *, accept: str) -> str:
        req = urllib.request.Request(
            url,
            headers={"Accept": accept, "User-Agent": USER_AGENT},
        )
        try:
            response: HTTPResponse
            if self._opener is not None:
                response = self._opener.open(req, timeout=self.timeout)
            else:
                response = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise BoeNotFoundError(f"404 en {url}") from exc
            # 5xx son transitorios; los relanzamos como URLError para que el
            # reintento los capture.
            if 500 <= exc.code < 600:
                raise urllib.error.URLError(f"HTTP {exc.code}") from exc
            raise BoeFetchError(f"HTTP {exc.code} en {url}: {exc.reason}") from exc

        with response:
            raw = response.read()
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)
