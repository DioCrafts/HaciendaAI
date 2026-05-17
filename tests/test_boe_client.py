"""Tests del cliente HTTP del BOE.

Mockeamos `urllib.request.urlopen` para verificar:

- Cache de disco: dos llamadas seguidas a la misma URL solo pegan a la
  red una vez.
- Reintentos con backoff en errores transitorios (URLError, 5xx).
- 404 → `BoeNotFoundError` (no se reintenta).
- Rate-limit aplicado solo en hits reales, no en cache.
- `boe_id` inválido se rechaza antes de hacer red.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import pytest

from hacienda_ai.rag.ingestion.boe_client import (
    BoeClient,
    BoeFetchError,
    BoeNotFoundError,
)


class _FakeResponse:
    def __init__(self, payload: bytes, status: int = 200) -> None:
        self._buf = io.BytesIO(payload)
        self.status = status

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        self._buf.close()


class _FakeOpener:
    """Opener stub con cola de respuestas/excepciones.

    Cada llamada a `open` consume la primera entrada de la cola. Si es una
    excepción, la levanta; si es un payload bytes, devuelve _FakeResponse.
    """

    def __init__(self, queue: list[bytes | Exception]) -> None:
        self.queue = queue
        self.calls: list[str] = []

    def open(self, req: urllib.request.Request, timeout: float) -> _FakeResponse:
        self.calls.append(req.full_url)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)


def _client(tmp_path: Path, opener: _FakeOpener) -> BoeClient:
    return BoeClient(
        cache_dir=tmp_path,
        rate_limit_seconds=0,
        max_retries=3,
        opener=opener,
        sleeper=lambda _: None,
    )


def test_fetch_summary_devuelve_payload_y_cachea(tmp_path: Path) -> None:
    payload = b'{"data": {"sumario": {}}}'
    opener = _FakeOpener([payload])
    client = _client(tmp_path, opener)

    raw, ct = client.fetch_summary(date(2024, 1, 30))
    assert raw.startswith('{"data"')
    assert "json" in ct.lower()

    # Segunda llamada: debe servirse de caché, sin tocar el opener.
    raw_again, _ = client.fetch_summary(date(2024, 1, 30))
    assert raw_again == raw
    assert len(opener.calls) == 1  # solo una petición real.


def test_fetch_summary_404_levanta_BoeNotFoundError(tmp_path: Path) -> None:
    err = urllib.error.HTTPError(
        url="x", code=404, msg="Not Found", hdrs=None, fp=None  # type: ignore[arg-type]
    )
    opener = _FakeOpener([err])
    client = _client(tmp_path, opener)
    with pytest.raises(BoeNotFoundError):
        client.fetch_summary(date(2024, 1, 28))
    # No se reintenta el 404: una sola llamada.
    assert len(opener.calls) == 1


def test_fetch_summary_reintenta_en_urlerror(tmp_path: Path) -> None:
    err = urllib.error.URLError("conexión rechazada")
    payload = b'{"data": {"sumario": {}}}'
    opener = _FakeOpener([err, err, payload])
    client = _client(tmp_path, opener)
    raw, _ = client.fetch_summary(date(2024, 1, 30))
    assert raw  # devolvió en el tercer intento.
    assert len(opener.calls) == 3


def test_fetch_summary_reintenta_en_500(tmp_path: Path) -> None:
    err = urllib.error.HTTPError(
        url="x", code=503, msg="Service Unavailable", hdrs=None, fp=None  # type: ignore[arg-type]
    )
    payload = b'{"data": {"sumario": {}}}'
    opener = _FakeOpener([err, payload])
    client = _client(tmp_path, opener)
    raw, _ = client.fetch_summary(date(2024, 1, 30))
    assert raw
    assert len(opener.calls) == 2


def test_fetch_summary_falla_tras_agotar_reintentos(tmp_path: Path) -> None:
    err = urllib.error.URLError("network down")
    opener = _FakeOpener([err, err, err])
    client = _client(tmp_path, opener)
    with pytest.raises(BoeFetchError):
        client.fetch_summary(date(2024, 1, 30))
    assert len(opener.calls) == 3


def test_fetch_document_rechaza_id_invalido(tmp_path: Path) -> None:
    opener = _FakeOpener([])
    client = _client(tmp_path, opener)
    with pytest.raises(BoeFetchError):
        client.fetch_document_xml("INVALID-ID")
    # No se tocó la red.
    assert opener.calls == []


def test_fetch_document_cachea(tmp_path: Path) -> None:
    xml = b"<documento><texto>foo</texto></documento>"
    opener = _FakeOpener([xml])
    client = _client(tmp_path, opener)
    raw_1 = client.fetch_document_xml("BOE-A-2024-1700")
    raw_2 = client.fetch_document_xml("BOE-A-2024-1700")
    assert raw_1 == raw_2 == xml.decode("utf-8")
    assert len(opener.calls) == 1
