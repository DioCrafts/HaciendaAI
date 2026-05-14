"""Descarga y caché local de fuentes oficiales.

Diseño:
- Cada fuente del catálogo se guarda como `<id>.html` (o `.pdf`) en
  el directorio de caché. La metadata de la descarga (URL, fecha,
  tamaño, content-type) en `<id>.meta.json` junto al fichero.
- Si el fichero ya existe y `force=False`, se salta la descarga.
- HTTP via httpx con un User-Agent identificado para que los
  servidores puedan auditar el tráfico.
- Sin paralelismo agresivo: bucle secuencial con un pequeño delay
  entre llamadas para no martillear los servidores.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ..sources.catalog import CATALOG, OfficialSource

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "hacienda_ai" / "rag"
USER_AGENT = "HaciendaAI-RAG/0.1 (+https://github.com/diocrafts/haciendaai)"
HTTP_TIMEOUT_SECONDS = 30.0
DELAY_BETWEEN_FETCHES_SECONDS = 0.5


@dataclass(frozen=True)
class FetchResult:
    source_id: str
    path: Path
    size_bytes: int
    content_type: str
    fetched_at: str
    skipped: bool


def cached_path(cache_dir: Path, source: OfficialSource) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = ".pdf" if source.url.lower().endswith(".pdf") else ".html"
    return cache_dir / f"{source.id}{suffix}"


def metadata_path(content_path: Path) -> Path:
    return content_path.with_suffix(content_path.suffix + ".meta.json")


def fetch_source(
    source: OfficialSource,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    *,
    force: bool = False,
    client: httpx.Client | None = None,
) -> FetchResult:
    """Descarga una fuente al directorio de caché. Si ya está y `force` es
    False, devuelve un FetchResult con `skipped=True` reusando el fichero
    existente.
    """
    target = cached_path(cache_dir, source)
    meta_target = metadata_path(target)

    if target.exists() and meta_target.exists() and not force:
        meta = json.loads(meta_target.read_text(encoding="utf-8"))
        return FetchResult(
            source_id=source.id,
            path=target,
            size_bytes=int(meta.get("size_bytes", target.stat().st_size)),
            content_type=str(meta.get("content_type", "unknown")),
            fetched_at=str(meta.get("fetched_at", "unknown")),
            skipped=True,
        )

    owned_client = client is None
    http_client = client or httpx.Client(
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    )
    try:
        response = http_client.get(source.url)
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("content-type", "application/octet-stream")
    finally:
        if owned_client:
            http_client.close()

    target.write_bytes(content)
    fetched_at = datetime.now(tz=UTC).isoformat(timespec="seconds")
    result = FetchResult(
        source_id=source.id,
        path=target,
        size_bytes=len(content),
        content_type=content_type,
        fetched_at=fetched_at,
        skipped=False,
    )
    meta_target.write_text(json.dumps(asdict(result) | {"path": str(target)}, indent=2))
    return result


def fetch_all(
    cache_dir: Path = DEFAULT_CACHE_DIR,
    *,
    force: bool = False,
    delay: float = DELAY_BETWEEN_FETCHES_SECONDS,
    sources: tuple[OfficialSource, ...] = CATALOG,
) -> list[FetchResult]:
    results: list[FetchResult] = []
    with httpx.Client(
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for index, source in enumerate(sources):
            if index > 0 and delay > 0:
                time.sleep(delay)
            results.append(fetch_source(source, cache_dir, force=force, client=client))
    return results


def cache_status(
    cache_dir: Path = DEFAULT_CACHE_DIR, sources: tuple[OfficialSource, ...] = CATALOG
) -> list[dict[str, object]]:
    """Devuelve el estado del caché por fuente: presencia, fecha y tamaño."""
    status: list[dict[str, object]] = []
    for source in sources:
        target = cached_path(cache_dir, source)
        if target.exists():
            meta_target = metadata_path(target)
            meta: dict[str, object] = {}
            if meta_target.exists():
                meta = json.loads(meta_target.read_text(encoding="utf-8"))
            status.append(
                {
                    "id": source.id,
                    "cached": True,
                    "size_bytes": meta.get("size_bytes", target.stat().st_size),
                    "fetched_at": meta.get("fetched_at", "unknown"),
                    "path": str(target),
                }
            )
        else:
            status.append({"id": source.id, "cached": False})
    return status
