"""Cliente HTTP para descargar texto consolidado del BOE.

Endpoint:
    GET https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/{boe_id}/texto

Devuelve XML con la estructura completa: cabecera, articulado vivo
organizado por `<bloque>` (cada uno con varias `<version>` históricas) y
notas editoriales. Es muy distinto del XML publicado del día
(`/diario_boe/xml.php?id=`), que el módulo `rag/ingestion` ya usa para
hashear documentos en el momento de su publicación.

Diseño separado de `rag.ingestion.boe_client` por dos razones:
1. La cache lleva XML voluminoso (LIRPF ronda los 4 MB consolidados); va
   en `.cache/boe/consolidated/` para no mezclarse con sumarios y
   documentos del día.
2. La cache caduca: el consolidado del BOE puede cambiar en cualquier
   momento. Mantenemos un TTL configurable (por defecto, 1 día) tras el
   cual la cache se considera caducada y se vuelve a descargar.

Reintentos con backoff y 404 → `ConsolidatedFetchError` con contexto.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.client import HTTPResponse
from pathlib import Path
from typing import Callable, Protocol

USER_AGENT = (
    "hacienda-ai-consolidated/0.1 (+https://github.com/DioCrafts/HaciendaAI)"
)

CONSOLIDATED_URL = (
    "https://www.boe.es/datosabiertos/api/legislacion-consolidada/id/{boe_id}/texto"
)

DEFAULT_TIMEOUT = 30.0
DEFAULT_RATE_LIMIT_SECONDS = 0.2
DEFAULT_MAX_RETRIES = 3
DEFAULT_CACHE_TTL = timedelta(days=1)


class ConsolidatedFetchError(RuntimeError):
    """No se pudo obtener el texto consolidado del BOE."""


class _Opener(Protocol):
    """Interfaz mínima del opener HTTP para inyección en tests."""

    def open(
        self, req: urllib.request.Request, timeout: float
    ) -> HTTPResponse: ...


class ConsolidatedFetcher:
    """Descarga (con cache TTL) el XML consolidado de una norma BOE.

    `clock()` y `sleeper()` son inyectables para determinismo en tests
    (no esperas reales, no red real).
    """

    def __init__(
        self,
        *,
        cache_dir: Path,
        timeout: float = DEFAULT_TIMEOUT,
        rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        cache_ttl: timedelta = DEFAULT_CACHE_TTL,
        opener: _Opener | None = None,
        sleeper: Callable[[float], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.rate_limit_seconds = rate_limit_seconds
        self.max_retries = max_retries
        self.cache_ttl = cache_ttl
        self._opener: _Opener | None = opener
        self._sleep: Callable[[float], None] = (
            sleeper if sleeper is not None else time.sleep
        )
        # `clock` UTC para decidir caducidad. Por defecto, hora real.
        self._clock: Callable[[], datetime] = (
            clock if clock is not None else lambda: datetime.now(tz=timezone.utc)
        )

    def fetch(self, boe_id: str) -> str:
        """Devuelve el XML consolidado de la norma `boe_id`.

        Sirve desde caché si existe y no ha caducado. Si la caché está
        caducada, descarga y reemplaza. Si no había caché, descarga y
        crea.
        """
        if not boe_id.startswith("BOE-A-"):
            raise ConsolidatedFetchError(
                f"boe_id no es de norma estatal: {boe_id!r}"
            )

        cache_file = self.cache_dir / f"{boe_id}.xml"
        if cache_file.exists() and not self._is_stale(cache_file):
            return cache_file.read_text(encoding="utf-8")

        payload = self._get_with_retry(CONSOLIDATED_URL.format(boe_id=boe_id))
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(payload, encoding="utf-8")
        self._sleep(self.rate_limit_seconds)
        return payload

    def invalidate(self, boe_id: str) -> None:
        """Elimina la cache local de una norma. La próxima `fetch` redescarga.

        Lo invoca el detector de drift cuando confirma cambio legislativo,
        para que la siguiente comprobación parta del XML fresco del BOE.
        """
        cache_file = self.cache_dir / f"{boe_id}.xml"
        if cache_file.exists():
            cache_file.unlink()

    # ---------- Internals ----------

    def _is_stale(self, cache_file: Path) -> bool:
        # `cache_ttl == timedelta(0)` significa "siempre rancia" — útil
        # para forzar refresco sin tocar la cache desde fuera.
        if self.cache_ttl == timedelta(0):
            return True
        mtime = datetime.fromtimestamp(
            cache_file.stat().st_mtime, tz=timezone.utc
        )
        return self._clock() - mtime > self.cache_ttl

    def _get_with_retry(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._raw_get(url)
            except ConsolidatedFetchError:
                # 4xx definitivos (incl. 404), no reintentar.
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                self._sleep(2**attempt)
        raise ConsolidatedFetchError(
            f"GET {url} falló tras {self.max_retries} intentos: {last_error}"
        ) from last_error

    def _raw_get(self, url: str) -> str:
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/xml", "User-Agent": USER_AGENT},
        )
        try:
            response: HTTPResponse
            if self._opener is not None:
                response = self._opener.open(req, timeout=self.timeout)
            else:
                response = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise ConsolidatedFetchError(
                    f"404: no existe consolidado para {url}"
                ) from exc
            # 5xx son transitorios → relanzamos como URLError para reintentar.
            if 500 <= exc.code < 600:
                raise urllib.error.URLError(f"HTTP {exc.code}") from exc
            raise ConsolidatedFetchError(
                f"HTTP {exc.code} en {url}: {exc.reason}"
            ) from exc

        with response:
            raw = response.read()
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)
