"""Tests del módulo RAG: catálogo, fetcher con httpx mock, extracción
de texto, búsqueda y CLI."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from hacienda_ai.cli import main
from hacienda_ai.rag.ingestion.fetcher import (
    FetchResult,
    cache_status,
    cached_path,
    fetch_all,
    fetch_source,
    metadata_path,
)
from hacienda_ai.rag.ingestion.text import extract_text
from hacienda_ai.rag.retrieval.search import search
from hacienda_ai.rag.sources.catalog import CATALOG, OfficialSource

# ---------- catálogo ----------


def test_catalog_has_unique_ids() -> None:
    ids = [source.id for source in CATALOG]
    assert len(ids) == len(set(ids))


def test_catalog_covers_state_and_autonomic_sources() -> None:
    jurisdictions = {source.jurisdiction for source in CATALOG}
    assert "estatal" in jurisdictions
    assert any(j not in {"estatal"} for j in jurisdictions)


def test_catalog_urls_are_https() -> None:
    for source in CATALOG:
        assert source.url.startswith("https://"), f"{source.id}: la URL debe ser HTTPS"


def test_catalog_includes_known_state_laws() -> None:
    ids = {source.id for source in CATALOG}
    for required in ("es_lirpf", "es_reglamento_irpf", "es_ley_49_2002_mecenazgo"):
        assert required in ids


def test_catalog_includes_dgt_thematic_entries() -> None:
    """El catálogo debe incluir al menos un puñado de entradas DGT temáticas
    para que el CLI 'rag list --type=consulta_dgt' aporte valor."""
    dgt_entries = [s for s in CATALOG if s.document_type == "consulta_dgt"]
    assert len(dgt_entries) >= 8
    # Todas las consultas DGT son estatales (criterio doctrinal nacional).
    assert all(s.jurisdiction == "estatal" for s in dgt_entries)
    # Las URLs deben apuntar al portal Petete de la DGT.
    assert all("petete.tributos.hacienda.gob.es" in s.url for s in dgt_entries)


# ---------- fetcher (httpx mock) ----------


def _make_mock_client(responses: dict[str, tuple[int, bytes, str]]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/error":
            return httpx.Response(500, content=b"server error")
        for url_fragment, (status, body, content_type) in responses.items():
            if url_fragment in str(request.url):
                return httpx.Response(status, content=body, headers={"content-type": content_type})
        return httpx.Response(404, content=b"not found")

    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


def _example_source(url: str = "https://example.com/lirpf.html") -> OfficialSource:
    return OfficialSource(
        id="test_source",
        title="Test source",
        jurisdiction="estatal",
        document_type="ley",
        url=url,
    )


def test_fetch_source_downloads_and_caches(tmp_path: Path) -> None:
    source = _example_source()
    body = b"<html><body><h1>LIRPF art. 81</h1><p>Deduccion por maternidad.</p></body></html>"
    client = _make_mock_client({"lirpf.html": (200, body, "text/html")})
    result = fetch_source(source, tmp_path, client=client)
    client.close()
    assert result.skipped is False
    assert result.size_bytes == len(body)
    assert result.path.exists()
    assert result.path.read_bytes() == body
    meta = json.loads(metadata_path(result.path).read_text())
    assert meta["source_id"] == "test_source"


def test_fetch_source_skips_when_cached(tmp_path: Path) -> None:
    source = _example_source()
    body = b"<html>cached</html>"
    client = _make_mock_client({"lirpf.html": (200, body, "text/html")})
    fetch_source(source, tmp_path, client=client)
    second = fetch_source(source, tmp_path, client=client)
    client.close()
    assert second.skipped is True
    assert second.size_bytes == len(body)


def test_fetch_source_force_redownloads(tmp_path: Path) -> None:
    source = _example_source()
    body_v1 = b"<html>v1</html>"
    body_v2 = b"<html>v2 changed</html>"
    client_v1 = _make_mock_client({"lirpf.html": (200, body_v1, "text/html")})
    fetch_source(source, tmp_path, client=client_v1)
    client_v1.close()
    client_v2 = _make_mock_client({"lirpf.html": (200, body_v2, "text/html")})
    result = fetch_source(source, tmp_path, client=client_v2, force=True)
    client_v2.close()
    assert result.skipped is False
    assert result.path.read_bytes() == body_v2


def test_fetch_source_raises_on_http_error(tmp_path: Path) -> None:
    source = _example_source(url="https://example.com/error")
    client = _make_mock_client({})
    with pytest.raises(httpx.HTTPStatusError):
        fetch_source(source, tmp_path, client=client)
    client.close()


def test_cache_status_reports_missing_and_present(tmp_path: Path) -> None:
    sources = (_example_source(),)
    status_before = cache_status(tmp_path, sources=sources)
    assert status_before[0]["cached"] is False
    client = _make_mock_client({"lirpf.html": (200, b"<html>x</html>", "text/html")})
    fetch_source(sources[0], tmp_path, client=client)
    client.close()
    status_after = cache_status(tmp_path, sources=sources)
    assert status_after[0]["cached"] is True
    assert status_after[0]["size_bytes"] == len(b"<html>x</html>")


# ---------- extracción de texto ----------


def test_extract_text_strips_html(tmp_path: Path) -> None:
    path = tmp_path / "doc.html"
    path.write_bytes(
        b"<html><head><title>x</title><style>body{}</style></head>"
        b"<body><script>alert(1)</script><p>Articulo 19 LIRPF</p></body></html>"
    )
    text = extract_text(path)
    assert "Articulo 19 LIRPF" in text
    assert "alert" not in text  # script eliminado
    assert "body{}" not in text  # style eliminado


def test_extract_text_handles_txt(tmp_path: Path) -> None:
    path = tmp_path / "note.txt"
    path.write_text("Ley 35/2006 IRPF", encoding="utf-8")
    assert "Ley 35/2006" in extract_text(path)


def test_extract_text_pdf_returns_placeholder(tmp_path: Path) -> None:
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-binary")
    text = extract_text(path)
    assert "PDF no soportado" in text


# ---------- search ----------


def test_search_returns_hits_with_snippets(tmp_path: Path) -> None:
    source = _example_source()
    target = cached_path(tmp_path, source)
    target.write_bytes(
        b"<html><body><p>Articulo 19 de la Ley 35/2006: cuotas sindicales son gasto deducible "
        b"de los rendimientos del trabajo.</p></body></html>"
    )
    metadata_path(target).write_text(json.dumps({"source_id": source.id, "size_bytes": 100, "fetched_at": "x"}))
    hits = search("cuotas sindicales", cache_dir=tmp_path, sources=(source,))
    assert len(hits) == 1
    assert hits[0].source.id == "test_source"
    assert "cuotas sindicales" in hits[0].snippet.lower()


def test_search_returns_empty_when_no_terms(tmp_path: Path) -> None:
    hits = search("", cache_dir=tmp_path)
    assert hits == []


def test_search_skips_sources_not_in_cache(tmp_path: Path) -> None:
    source = _example_source()
    hits = search("lirpf", cache_dir=tmp_path, sources=(source,))
    assert hits == []


# ---------- CLI rag ----------


def test_cli_rag_list_prints_all_sources(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["rag", "list"])
    captured = capsys.readouterr()
    assert exit_code == 0
    for source in CATALOG:
        assert source.id in captured.out


def test_cli_rag_list_filters_by_jurisdiction(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["rag", "list", "--jurisdiction", "estatal"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "es_lirpf" in captured.out
    # Una autonómica no debe estar
    assert "auto_madrid_dlt" not in captured.out


def test_cli_rag_list_filters_by_doc_type_dgt(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["rag", "list", "--type", "consulta_dgt"])
    captured = capsys.readouterr()
    assert exit_code == 0
    # Sólo aparecen entradas con document_type=consulta_dgt
    assert "dgt_busqueda_planes_pensiones" in captured.out
    # Las leyes NO deben aparecer
    assert "es_lirpf" not in captured.out
    assert "auto_madrid_dlt" not in captured.out


def test_cli_rag_list_combines_jurisdiction_and_type_filters(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["rag", "list", "--jurisdiction", "estatal", "--type", "ley"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "es_lirpf" in captured.out
    # consulta_dgt no entra aunque sea estatal
    assert "dgt_busqueda_planes_pensiones" not in captured.out
    # No autonómicas
    assert "auto_madrid_dlt" not in captured.out


def test_cli_rag_status_reports_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["rag", "status", "--cache-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "missing" in captured.out
    assert "cached" not in captured.out


def test_cli_rag_fetch_without_flags_returns_2(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    exit_code = main(["rag", "fetch", "--cache-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "--all" in captured.err or "--id" in captured.err


def test_cli_rag_fetch_unknown_id_returns_2(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    exit_code = main(["rag", "fetch", "--id", "no_existe", "--cache-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert exit_code == 2
    assert "no_existe" in captured.err


def test_cli_rag_fetch_uses_real_http_when_called(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Para evitar tráfico real durante los tests, parcheamos
    `fetch_source` en el CLI con una versión que escribe un fichero
    falso. Solo verifica el cableado del CLI."""

    def fake_fetch(source: OfficialSource, cache_dir: Path, **_: Any) -> FetchResult:
        target = cached_path(cache_dir, source)
        target.write_bytes(b"<html>mock</html>")
        return FetchResult(
            source_id=source.id,
            path=target,
            size_bytes=len(b"<html>mock</html>"),
            content_type="text/html",
            fetched_at="2026-05-14T00:00:00+00:00",
            skipped=False,
        )

    import hacienda_ai.rag.ingestion.fetcher as fetcher_module

    monkeypatch.setattr(fetcher_module, "fetch_source", fake_fetch)
    exit_code = main(["rag", "fetch", "--id", "es_lirpf", "--cache-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "es_lirpf" in captured.out


def test_cli_rag_search_without_cache_says_no_hits(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["rag", "search", "cuotas", "--cache-dir", str(tmp_path)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Sin coincidencias" in captured.out


def test_fetch_all_with_pre_cached_source_skips_network(tmp_path: Path) -> None:
    """fetch_all reutiliza el caché sin tocar la red cuando todo está
    descargado previamente."""
    source = _example_source(url="https://example.com/lirpf.html")
    target = cached_path(tmp_path, source)
    target.write_bytes(b"<html>cached</html>")
    metadata_path(target).write_text(
        json.dumps({"source_id": source.id, "size_bytes": 17, "content_type": "text/html", "fetched_at": "x"})
    )
    results = fetch_all(tmp_path, delay=0.0, sources=(source,))
    assert len(results) == 1
    assert results[0].skipped is True
