"""Clientes para resolver número de consulta DGT a HTML.

Dos implementaciones del Protocol `DgtClient`:

1. **`LocalDgtClient`**: lee desde directorio local con un fichero por
   consulta (`V<NNNN>-<YY>.html`). Útil para CI/tests y para operadores
   que archiven lotes descargados manualmente del buscador Petete.

2. **`HttpDgtClient`**: cliente experimental contra Petete
   (https://petete.tributos.hacienda.gob.es). Rate-limit 3 s,
   User-Agent identificativo, retry con backoff. Marcado experimental:
   Petete no expone API REST oficial y el HTML del buscador puede
   cambiar.

El runner es agnóstico del cliente. Para añadir una fuente alternativa
(volcado oficial, dataset abierto) basta con implementar otro Protocol.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.client import HTTPResponse
from pathlib import Path
from typing import Callable, Protocol

from .numero import NumeroConsulta

USER_AGENT = (
    "hacienda-ai-dgt/0.1 "
    "(+https://github.com/DioCrafts/HaciendaAI; "
    "uso: copiloto fiscal con auditoría; "
    "rate-limit aplicado)"
)

# URL del buscador Petete que abre una consulta concreta. El parámetro
# `num_consulta` acepta tanto la forma corta como la larga del número.
_PETETE_URL_TEMPLATE = (
    "https://petete.tributos.hacienda.gob.es/consultas/?num_consulta={numero}"
)


class DgtFetchError(RuntimeError):
    """Error de red, formato o autorización al obtener consulta de Petete."""


@dataclass(frozen=True)
class DgtSearchResult:
    """Resultado mínimo de búsqueda; mismo patrón que CENDOJ."""

    numero: NumeroConsulta
    asunto: str | None
    url: str | None


class DgtClient(Protocol):
    """Contrato mínimo para resolver consultas DGT."""

    def fetch_full(self, numero: NumeroConsulta) -> str:
        """Devuelve HTML completo de la consulta `numero`.

        Lanza `DgtFetchError` ante 404, error de red o formato.
        """
        ...


# ---------- LocalDgtClient ----------


class LocalDgtClient:
    """Lee consultas desde un directorio local.

    Espera ficheros con nombre `<canonical>.html` (`V0123-24.html`) o
    `<long_form>.html` (`V0123-2024.html`). Permite ambas porque los
    operadores que archivan manualmente pueden usar cualquiera de las
    dos formas.
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    def fetch_full(self, numero: NumeroConsulta) -> str:
        candidates = [
            self.root_dir / f"{numero.canonical}.html",
            self.root_dir / f"{numero.long_form}.html",
            self.root_dir / f"{numero.canonical}.xml",
            self.root_dir / f"{numero.long_form}.xml",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        raise DgtFetchError(
            f"no se encontró consulta local para {numero.canonical} "
            f"en {self.root_dir} (buscado: {[c.name for c in candidates]})"
        )


# ---------- HttpDgtClient ----------


class _Opener(Protocol):
    """Interfaz mínima del opener HTTP para inyección en tests."""

    def open(
        self, req: urllib.request.Request, timeout: float
    ) -> HTTPResponse: ...


class HttpDgtClient:
    """Cliente experimental contra Petete (Ministerio de Hacienda).

    Rate-limit conservador, User-Agent identificativo, cache de disco.
    Si Petete cambia su HTML, este cliente sigue trayendo bytes; el
    parser posterior puede fallar — responsabilidad de `parser.py`.
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

    def fetch_full(self, numero: NumeroConsulta) -> str:
        cache_key = self.cache_dir / f"{numero.canonical}.html"
        if cache_key.exists():
            return cache_key.read_text(encoding="utf-8")

        url = _PETETE_URL_TEMPLATE.format(numero=numero.canonical)
        payload = self._get_with_retry(url)
        cache_key.parent.mkdir(parents=True, exist_ok=True)
        cache_key.write_text(payload, encoding="utf-8")
        self._sleep(self.rate_limit_seconds)
        return payload

    # ---------- Internals ----------

    def _get_with_retry(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return self._raw_get(url)
            except DgtFetchError:
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                self._sleep(2**attempt)
        raise DgtFetchError(
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
                raise DgtFetchError(f"404 en {url}") from exc
            if 500 <= exc.code < 600:
                raise urllib.error.URLError(f"HTTP {exc.code}") from exc
            raise DgtFetchError(
                f"HTTP {exc.code} en {url}: {exc.reason}"
            ) from exc

        with response:
            raw = response.read()
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)
