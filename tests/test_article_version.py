"""Tests del modelo VersionArticulo y ArticleRegistry."""

from __future__ import annotations

import hashlib
from datetime import date

import pytest

from hacienda_ai.models import (
    ArticleRegistry,
    ValidationError,
    VersionArticulo,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_version(
    *,
    article_id: str = "a23",
    norma_boe_id: str = "BOE-A-2006-20764",
    effective_from: date = date(2007, 1, 1),
    effective_to: date | None = None,
    text: str = "Texto del artículo.",
    modified_by_boe_id: str | None = None,
) -> VersionArticulo:
    return VersionArticulo(
        norma_boe_id=norma_boe_id,
        article_id=article_id,
        effective_from=effective_from,
        effective_to=effective_to,
        text=text,
        text_hash=_sha(text),
        modified_by_boe_id=modified_by_boe_id,
    )


# ---------- VersionArticulo ----------


def test_version_articulo_construye_ok() -> None:
    v = _make_version()
    assert v.norma_boe_id == "BOE-A-2006-20764"
    assert v.article_id == "a23"
    assert v.effective_to is None


def test_version_articulo_rechaza_effective_to_anterior_a_from() -> None:
    with pytest.raises(ValidationError):
        VersionArticulo(
            norma_boe_id="BOE-A-2006-20764",
            article_id="a23",
            effective_from=date(2020, 1, 1),
            effective_to=date(2019, 12, 31),
            text="x",
            text_hash=_sha("x"),
        )


def test_version_articulo_rechaza_hash_de_longitud_incorrecta() -> None:
    with pytest.raises(ValidationError):
        VersionArticulo(
            norma_boe_id="BOE-A-2006-20764",
            article_id="a23",
            effective_from=date(2020, 1, 1),
            effective_to=None,
            text="x",
            text_hash="abc123",
        )


def test_version_articulo_rechaza_hash_no_hex() -> None:
    with pytest.raises(ValidationError):
        VersionArticulo(
            norma_boe_id="BOE-A-2006-20764",
            article_id="a23",
            effective_from=date(2020, 1, 1),
            effective_to=None,
            text="x",
            text_hash="g" * 64,  # 'g' no es hex
        )


def test_version_articulo_covers_within_interval() -> None:
    v = _make_version(
        effective_from=date(2015, 1, 1),
        effective_to=date(2020, 12, 31),
    )
    assert v.covers(date(2017, 6, 1))
    assert v.covers(date(2015, 1, 1))
    assert v.covers(date(2020, 12, 31))
    assert not v.covers(date(2014, 12, 31))
    assert not v.covers(date(2021, 1, 1))


def test_version_articulo_covers_open_ended() -> None:
    v = _make_version(
        effective_from=date(2015, 1, 1),
        effective_to=None,
    )
    assert v.covers(date(2099, 1, 1))
    assert not v.covers(date(2014, 12, 31))


def test_version_articulo_roundtrip_dict() -> None:
    v = _make_version(modified_by_boe_id="BOE-A-2014-12328")
    restored = VersionArticulo.from_dict(v.to_dict())
    assert restored == v


def test_version_articulo_to_dict_omite_campos_none() -> None:
    """`effective_to` y `modified_by_boe_id` deben omitirse del JSON
    cuando son None, para mantener output limpio."""
    v = _make_version()  # ambos None
    d = v.to_dict()
    assert "effective_to" not in d
    assert "modified_by_boe_id" not in d


# ---------- ArticleRegistry ----------


def test_registry_register_and_lookup() -> None:
    reg = ArticleRegistry()
    v1 = _make_version(
        effective_from=date(2007, 1, 1),
        effective_to=date(2014, 12, 31),
        text="old",
    )
    v2 = _make_version(
        effective_from=date(2015, 1, 1),
        effective_to=None,
        text="new",
    )
    reg.register(v1)
    reg.register(v2)

    assert reg.knows_article("BOE-A-2006-20764", "a23")
    assert reg.total_versions == 2

    found = reg.version_at("BOE-A-2006-20764", "a23", date(2010, 1, 1))
    assert found is not None
    assert found.text == "old"

    found = reg.version_at("BOE-A-2006-20764", "a23", date(2020, 1, 1))
    assert found is not None
    assert found.text == "new"


def test_registry_version_at_returns_none_for_unknown_article() -> None:
    reg = ArticleRegistry()
    assert reg.version_at("BOE-A-2006-20764", "a999", date(2020, 1, 1)) is None


def test_registry_version_at_returns_none_for_future_date() -> None:
    reg = ArticleRegistry()
    reg.register(_make_version(effective_from=date(2020, 1, 1)))
    assert reg.version_at("BOE-A-2006-20764", "a23", date(2019, 1, 1)) is None


def test_registry_register_rejects_overlap() -> None:
    """Si BOE consolidado produce dos versiones del mismo artículo con
    intervalos solapados, es un error del corpus — abortamos."""
    reg = ArticleRegistry()
    reg.register(
        _make_version(
            effective_from=date(2015, 1, 1),
            effective_to=date(2020, 12, 31),
            text="a",
        )
    )
    with pytest.raises(ValidationError, match="Solapamiento"):
        reg.register(
            _make_version(
                effective_from=date(2018, 1, 1),
                effective_to=None,
                text="b",
            )
        )


def test_registry_versions_for_returns_ordered() -> None:
    """Las versiones se devuelven ordenadas por `effective_from`
    independientemente del orden de inserción."""
    reg = ArticleRegistry()
    v2 = _make_version(
        effective_from=date(2015, 1, 1),
        effective_to=None,
        text="new",
    )
    v1 = _make_version(
        effective_from=date(2007, 1, 1),
        effective_to=date(2014, 12, 31),
        text="old",
    )
    reg.register(v2)
    reg.register(v1)
    versions = reg.versions_for("BOE-A-2006-20764", "a23")
    assert [v.effective_from for v in versions] == [
        date(2007, 1, 1),
        date(2015, 1, 1),
    ]


def test_registry_all_articles_for_filters_by_norma() -> None:
    reg = ArticleRegistry()
    reg.register(_make_version(article_id="a23"))
    reg.register(_make_version(article_id="a81bis"))
    reg.register(
        _make_version(
            article_id="a10",
            norma_boe_id="BOE-A-2014-12328",
        )
    )
    assert reg.all_articles_for("BOE-A-2006-20764") == ("a23", "a81bis")
    assert reg.all_articles_for("BOE-A-2014-12328") == ("a10",)
    assert reg.all_articles_for("BOE-A-9999-9999") == ()


def test_registry_all_norma_boe_ids() -> None:
    reg = ArticleRegistry()
    reg.register(_make_version(article_id="a23"))
    reg.register(
        _make_version(
            article_id="a10",
            norma_boe_id="BOE-A-2014-12328",
        )
    )
    assert reg.all_norma_boe_ids() == (
        "BOE-A-2006-20764",
        "BOE-A-2014-12328",
    )


def test_registry_roundtrip_dict() -> None:
    reg = ArticleRegistry()
    reg.register(
        _make_version(
            article_id="a23",
            effective_from=date(2007, 1, 1),
            effective_to=date(2014, 12, 31),
            text="old",
        )
    )
    reg.register(
        _make_version(
            article_id="a23",
            effective_from=date(2015, 1, 1),
            effective_to=None,
            text="new",
            modified_by_boe_id="BOE-A-2014-12328",
        )
    )
    restored = ArticleRegistry.from_dict(reg.to_dict())
    assert restored.total_versions == 2
    found = restored.version_at(
        "BOE-A-2006-20764", "a23", date(2020, 1, 1)
    )
    assert found is not None
    assert found.text == "new"
    assert found.modified_by_boe_id == "BOE-A-2014-12328"


def test_registry_from_dict_rejects_invalid_versions_key() -> None:
    with pytest.raises(ValidationError):
        ArticleRegistry.from_dict({"versions": "not a list"})
