"""Clientes para resolver número de reclamación TEAC/TEAR a HTML.

Dos implementaciones del Protocol `TeacClient`, mismo patrón que en
los pipelines de CENDOJ y DGT:

1. **`LocalTeacClient`**: lee desde directorio local con un fichero
   por resolución (`<canonical>.html` o variantes con `/` reemplazado
   por `_`). El `/` es ilegal en nombres de fichero, así que el sistema
   sustituye `/` por `_` al buscar (no toca el canonical en memoria).

2. **`HttpTeacClient`**: cliente experimental contra el buscador
   doctrinal del Ministerio. Rate-limit 3 s, User-Agent identificativo,
   retry con backoff. Marcado experimental: el HTML del buscador puede
   cambiar.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from http.client import HTTPResponse
from pathlib import Path
from typing import Callable, Protocol

from .numero import NumeroReclamacion

USER_AGENT = (
    "hacienda-ai-teac/0.1 "
    "(+https://github.com/DioCrafts/HaciendaAI; "
    "uso: copiloto fiscal con auditoría; "
    "rate-limit aplicado)"
)

# Endpoint del buscador de doctrina y criterios. El parámetro `expediente`
# acepta el formato canónico TEAC.
_DYCTEA_URL_TEMPLATE = (
    "https://serviciostelematicos.minhap.gob.es/DYCTEA/criterio/"
    "buscar?expediente={numero}"
)


class TeacFetchError(RuntimeError):
    """Error de red, formato o autorización al obtener resolución TEAC."""


def _safe_name(canonical: str) -> str:
    """Convierte canónico `00/12345/2023` a un nombre filesystem-safe.

    Reemplaza `/` por `_` (los demás caracteres son seguros). El canónico
    en memoria no se toca: solo se usa esta forma para localizar el
    fichero local.
    """
    return canonical.replace("/", "_")


class TeacClient(Protocol):
    """Contrato mínimo para resolver resoluciones TEAC/TEAR."""

    def fetch_full(self, numero: NumeroReclamacion) -> str:
        """Devuelve HTML completo de la resolución.

        Lanza `TeacFetchError` ante 404, error de red o formato.
        """
        ...


# ---------- LocalTeacClient ----------


class LocalTeacClient:
    """Lee resoluciones desde un directorio local.

    Acepta dos formatos de nombre: el canónico con `/` reemplazado por
    `_` (`00_12345_2023.html`) y, para flexibilidad, también con `-`
    (`00-12345-2023.html`). Esto permite a operadores archivar con
    cualquiera de los dos convenios.
    """

    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    def fetch_full(self, numero: NumeroReclamacion) -> str:
        safe = _safe_name(numero.canonical)
        with_dashes = safe.replace("_", "-")
        candidates = [
            self.root_dir / f"{safe}.html",
            self.root_dir / f"{safe}.xml",
            self.root_dir / f"{with_dashes}.html",
            self.root_dir / f"{with_dashes}.xml",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        raise TeacFetchError(
            f"no se encontró resolución local para {numero.canonical} "
            f"en {self.root_dir} (buscado: {[c.name for c in candidates]})"
        )


# ---------- HttpTeacClient ----------


class _Opener(Protocol):
    """Interfaz mínima del opener HTTP para inyección en tests."""

    def open(
        self, req: urllib.request.Request, timeout: float
    ) -> HTTPResponse: ...


class HttpTeacClient:
    """Cliente experimental contra DYCTEA (buscador del Ministerio).

    Rate-limit conservador, User-Agent identificativo, cache de disco
    (igual patrón que CENDOJ/DGT).
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

    def fetch_full(self, numero: NumeroReclamacion) -> str:
        cache_key = self.cache_dir / f"{_safe_name(numero.canonical)}.html"
        if cache_key.exists():
            return cache_key.read_text(encoding="utf-8")

        url = _DYCTEA_URL_TEMPLATE.format(numero=numero.canonical)
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
            except TeacFetchError:
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt == self.max_retries - 1:
                    break
                self._sleep(2**attempt)
        raise TeacFetchError(
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
                raise TeacFetchError(f"404 en {url}") from exc
            if 500 <= exc.code < 600:
                raise urllib.error.URLError(f"HTTP {exc.code}") from exc
            raise TeacFetchError(f"HTTP {exc.code} en {url}: {exc.reason}") from exc

        with response:
            raw = response.read()
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)
