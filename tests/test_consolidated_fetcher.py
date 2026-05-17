"""Tests del fetcher de consolidado BOE."""

from __future__ import annotations

import io
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hacienda_ai.rag.consolidated import (
    ConsolidatedFetcher,
    ConsolidatedFetchError,
)


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._buf = io.BytesIO(payload)
        self.status = 200

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self._buf.close()


class _FakeOpener:
    def __init__(self, queue: list[bytes | Exception]) -> None:
        self.queue = queue
        self.calls: list[str] = []

    def open(self, req: urllib.request.Request, timeout: float) -> _FakeResponse:
        self.calls.append(req.full_url)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


def _fetcher(
    tmp_path: Path,
    opener: _FakeOpener,
    *,
    ttl_seconds: int = 3600,
    clock_now: datetime | None = None,
) -> ConsolidatedFetcher:
    return ConsolidatedFetcher(
        cache_dir=tmp_path,
        rate_limit_seconds=0,
        max_retries=3,
        cache_ttl=timedelta(seconds=ttl_seconds),
        opener=opener,
        sleeper=lambda _: None,
        clock=(lambda: clock_now) if clock_now else None,
    )


def test_fetch_devuelve_xml_y_cachea(tmp_path: Path) -> None:
    xml = b"<legislacion-consolidada><texto/></legislacion-consolidada>"
    opener = _FakeOpener([xml])
    f = _fetcher(tmp_path, opener)
    payload_1 = f.fetch("BOE-A-2006-20764")
    payload_2 = f.fetch("BOE-A-2006-20764")
    assert payload_1 == payload_2 == xml.decode("utf-8")
    # Una sola petición real: la segunda se sirvió de cache.
    assert len(opener.calls) == 1


def test_fetch_redescarga_si_cache_caduca(tmp_path: Path) -> None:
    xml1 = b"<legislacion-consolidada>v1</legislacion-consolidada>"
    xml2 = b"<legislacion-consolidada>v2</legislacion-consolidada>"
    opener = _FakeOpener([xml1, xml2])
    # TTL muy corto + reloj que avanza para forzar caducidad.
    base = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    f = ConsolidatedFetcher(
        cache_dir=tmp_path,
        rate_limit_seconds=0,
        max_retries=3,
        cache_ttl=timedelta(seconds=1),
        opener=opener,
        sleeper=lambda _: None,
        clock=lambda: base + timedelta(hours=2),
    )
    assert f.fetch("BOE-A-2006-20764") == xml1.decode("utf-8")
    # Forzamos el mtime del cache al pasado para simular caducidad.
    cache_file = tmp_path / "BOE-A-2006-20764.xml"
    past = (base - timedelta(hours=24)).timestamp()
    os.utime(cache_file, (past, past))
    # Segunda llamada: cache caducada → redescarga.
    assert f.fetch("BOE-A-2006-20764") == xml2.decode("utf-8")
    assert len(opener.calls) == 2


def test_fetch_rechaza_boletines_no_estatales(tmp_path: Path) -> None:
    f = _fetcher(tmp_path, _FakeOpener([]))
    with pytest.raises(ConsolidatedFetchError):
        f.fetch("BOCM-2024-1")
    with pytest.raises(ConsolidatedFetchError):
        f.fetch("DOGC-2024-1")


def test_fetch_404_no_se_reintenta(tmp_path: Path) -> None:
    err = urllib.error.HTTPError(
        url="x", code=404, msg="Not Found", hdrs=None, fp=None  # type: ignore[arg-type]
    )
    opener = _FakeOpener([err])
    f = _fetcher(tmp_path, opener)
    with pytest.raises(ConsolidatedFetchError):
        f.fetch("BOE-A-2099-99999")
    assert len(opener.calls) == 1


def test_fetch_reintenta_en_500(tmp_path: Path) -> None:
    err = urllib.error.HTTPError(
        url="x", code=503, msg="busy", hdrs=None, fp=None  # type: ignore[arg-type]
    )
    payload = b"<legislacion-consolidada/>"
    opener = _FakeOpener([err, payload])
    f = _fetcher(tmp_path, opener)
    f.fetch("BOE-A-2006-20764")
    assert len(opener.calls) == 2


def test_invalidate_borra_cache(tmp_path: Path) -> None:
    xml1 = b"v1"
    xml2 = b"v2"
    opener = _FakeOpener([xml1, xml2])
    f = _fetcher(tmp_path, opener)
    assert f.fetch("BOE-A-2006-20764") == "v1"
    f.invalidate("BOE-A-2006-20764")
    # Tras invalidate, la siguiente fetch baja XML fresco.
    assert f.fetch("BOE-A-2006-20764") == "v2"
    assert len(opener.calls) == 2


def test_invalidate_de_norma_no_cacheada_es_noop(tmp_path: Path) -> None:
    f = _fetcher(tmp_path, _FakeOpener([]))
    # No debe lanzar.
    f.invalidate("BOE-A-2099-99999")


def test_fetch_falla_tras_agotar_reintentos(tmp_path: Path) -> None:
    err = urllib.error.URLError("network down")
    opener = _FakeOpener([err, err, err])
    f = _fetcher(tmp_path, opener)
    with pytest.raises(ConsolidatedFetchError):
        f.fetch("BOE-A-2006-20764")
    assert len(opener.calls) == 3
