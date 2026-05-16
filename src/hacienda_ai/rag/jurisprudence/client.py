"""Clientes para resolver ECLI a HTML/XML de la sentencia.

Dos implementaciones del Protocol `CendojClient`:

1. **`LocalCendojClient`**: lee desde un directorio local con un fichero
   por ECLI (`<ECLI canónico>.html`). Útil para CI, tests, y para
   operadores que archiven manualmente lotes descargados del buscador
   del CGPJ.

2. **`HttpCendojClient`**: cliente experimental contra el buscador
   público del CGPJ. CENDOJ no tiene API REST oficial, así que esto
   es scraping defensivo:
   - Rate-limit conservador (1 req cada 3 s por defecto).
   - User-Agent identificativo y verificable (apunta al repo).
   - 3 reintentos con backoff exponencial.
   - Cache local: cada ECLI descargado queda en `cache_dir/<ECLI>.html`.

El cliente HTTP requiere mantenimiento: si el CGPJ cambia el HTML del
buscador, hay que actualizar `_SEARCH_URL_TEMPLATE` y posiblemente el
parser. Está documentado como experimental por eso.

El resto del pipeline (parser, filtro, runner) habla con el Protocol;
añadir un tercer cliente (volcado oficial, dataset abierto) es
inyectarlo sin tocar nada más.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.client import HTTPResponse
from pathlib import Path
from typing import Callable, Protocol

from .ecli import ECLI

USER_AGENT = (
    "hacienda-ai-cendoj/0.1 "
    "(+https://github.com/DioCrafts/HaciendaAI; "
    "uso: copiloto fiscal con auditoría; "
    "rate-limit aplicado)"
)

# URL del buscador del CGPJ que abre un documento por id interno. CENDOJ
# resuelve ECLI internamente cuando se pasa como parámetro `cendoj` del
# buscador. Esto es lo que está documentado públicamente; cualquier cambio
# del CGPJ a este endpoint requiere actualización.
_SEARCH_URL_TEMPLATE = (
    "https://www.poderjudicial.es/search/AN/openDocument/{ecli}"
)


class CendojFetchError(RuntimeError):
    """Error de red, formato o autorización al obtener sentencia de CENDOJ."""


@dataclass(frozen=True)
class CendojSearchResult:
    """Resultado mínimo de una búsqueda: lo que el pipeline necesita.

    No incluimos el texto completo aquí: el caller lo solicita con
    `fetch_full(result)` cuando decide que merece la pena.
    """

    ecli: ECLI
    titulo: str | None
    url: str | None


class CendojClient(Protocol):
    """Contrato mínimo para resolver ECLIs."""

    def fetch_full(self, ecli: ECLI) -> str:
        """Devuelve HTML (o XML) completo de la sentencia identificada por `ecli`.

        Lanza `CendojFetchError` ante 404, error de red o formato.
        """
        ...


# ---------- LocalCendojClient ----------


class LocalCendojClient:
    """Lee sentencias desde un directorio local. Útil para CI y backfills.

    Espera ficheros con nombre `<ECLI canónico>.html` (o `.xml`). El
    canónico es el devuelto por `ECLI.canonical`: `ECLI:ES:<trib>:<año>:<id>`,
    sin sufijos. Los caracteres `:` son legales en filesystems POSIX y en
    NTFS si se manejan con cuidado; para máxima compatibilidad reemplazamos
    `:` por `_` al buscar el fichero.
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    def fetch_full(self, ecli: ECLI) -> str:
        candidates = [
            self.root_dir / f"{ecli.canonical}.html",
            self.root_dir / f"{ecli.canonical}.xml",
            self.root_dir / f"{ecli.canonical.replace(':', '_')}.html",
            self.root_dir / f"{ecli.canonical.replace(':', '_')}.xml",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        raise CendojFetchError(
            f"no se encontró sentencia local para {ecli.canonical} "
            f"en {self.root_dir} (buscado: {[c.name for c in candidates]})"
        )


# ---------- HttpCendojClient ----------


class _Opener(Protocol):
    """Interfaz mínima del opener HTTP para inyección en tests."""

    def open(
        self, req: urllib.request.Request, timeout: float
    ) -> HTTPResponse: ...


class HttpCendojClient:
    """Cliente experimental contra el buscador público del CGPJ.

    Rate-limit conservador, identificación clara, cache de disco. Si el
    HTML del CGPJ cambia, este cliente sigue trayendo el contenido (es
    una cadena bytes), pero el parser posterior puede fallar — esa
    responsabilidad es del módulo `parser.py`.
    """

    def __init__(
        self,
        *,
        cache_dir: Path,
        timeout: float = 30.0,
        rate_limit_seconds: float = 3.0,
        max_retries: int = 3,
        opener: _Opener | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.rate_limit_seconds = rate_limit_seconds
        self.max_retries = max_retries
        self._opener: _Opener | None = opener
        self._sleep: Callable[[float], None] = (
            sleeper if sleeper is not None else time.sleep
        )

    def fetch_full(self, ecli: ECLI) -> str:
        cache_key = self.cache_dir / f"{ecli.canonical.replace(':', '_')}.html"
        if cache_key.exists():
            return cache_key.read_text(encoding="utf-8")

        url = _SEARCH_URL_TEMPLATE.format(ecli=ecli.canonical)
        payload = self._get_with_retry(url)
        cache_key.parent.mkdir(parents=True, exist_ok=True)
        cache_key.write_text(payload, encoding="utf-8")
        # Rate-limit solo tras peticiones reales, no en hits de caché.
        self._sleep(self.rate_limit_seconds)
        return payload

    # ---------- Internals ----------

    def _get_with_retry(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._raw_get(url)
            except CendojFetchError:
                # 4xx definitivos no se reintentan.
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                self._sleep(2**attempt)
        raise CendojFetchError(
            f"GET {url} falló tras {self.max_retries} intentos: {last_error}"
        ) from last_error

    def _raw_get(self, url: str) -> str:
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "es-ES,es;q=0.9",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            response: HTTPResponse
            if self._opener is not None:
                response = self._opener.open(req, timeout=self.timeout)
            else:
                response = urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise CendojFetchError(f"404 en {url}") from exc
            if 500 <= exc.code < 600:
                raise urllib.error.URLError(f"HTTP {exc.code}") from exc
            raise CendojFetchError(
                f"HTTP {exc.code} en {url}: {exc.reason}"
            ) from exc

        with response:
            raw = response.read()
        if isinstance(raw, bytes):
            # CENDOJ devuelve UTF-8 declarado en cabecera HTML; con errors
            # "replace" cualquier byte corrupto se sustituye sin romper.
            return raw.decode("utf-8", errors="replace")
        return str(raw)
