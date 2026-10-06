"""Unit tests for the Autocomplete + Wikipedia keyword validator.

All HTTP traffic is faked with ``httpx.MockTransport``; no real network.
Every test patches the ``httpx.AsyncClient`` constructor used inside the
validator so the transport and headers are under our control.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from unittest.mock import patch

import httpx
import pytest

from ideascroller.validators import keywords as keywords_module
from ideascroller.validators.keywords import (
    KeywordSignal,
    check_keyword,
    check_keyword_async,
)


# --- Mock transport helpers ---------------------------------------------


Handler = Callable[[httpx.Request], httpx.Response]


def _autocomplete_body(keyword: str, suggestions: list[str]) -> str:
    """Build a Google-Autocomplete-shaped JSON array literal."""
    return json.dumps([keyword, suggestions, [], {}])


def _wikipedia_body(view_counts: list[int]) -> dict:
    """Build a minimal Wikipedia pageviews payload."""
    items = [{"views": v, "timestamp": f"202{i}01010000"} for i, v in enumerate(view_counts)]
    return {"items": items}


def _route_handler(
    *,
    autocomplete: httpx.Response,
    wikipedia: httpx.Response,
) -> Handler:
    """Return a MockTransport handler dispatching by host."""

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if "suggestqueries.google.com" in host:
            return autocomplete
        if "wikimedia.org" in host:
            return wikipedia
        raise AssertionError(f"Unexpected host: {host}")

    return handler


_REAL_ASYNC_CLIENT = httpx.AsyncClient


class _ClientFactory:
    """Patch replacement for ``httpx.AsyncClient`` that injects a transport."""

    def __init__(self, handler: Handler) -> None:
        self._handler = handler

    def __call__(self, *args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(self._handler)
        return _REAL_ASYNC_CLIENT(**kwargs)  # type: ignore[arg-type]


def _patch_client(handler: Handler):
    """Patch httpx.AsyncClient inside the validator module."""
    return patch.object(keywords_module.httpx, "AsyncClient", _ClientFactory(handler))


# --- Verdict tests -------------------------------------------------------


@pytest.mark.unit
async def test_autocomplete_with_wiki_growing_tier() -> None:
    """10K–100K monthly views with phrase in suggestions → 'growing'."""
    keyword = "calorie counter"
    suggestions = ["calorie counter app", "calorie counter watch"]
    autocomplete = httpx.Response(200, text=_autocomplete_body(keyword, suggestions))
    # Total 12,500 → inside 10K–100K band.
    wikipedia = httpx.Response(200, json=_wikipedia_body([4_000, 4_500, 4_000]))

    handler = _route_handler(autocomplete=autocomplete, wikipedia=wikipedia)
    with _patch_client(handler):
        signal = await check_keyword_async(keyword)

    assert isinstance(signal, KeywordSignal)
    assert signal.checked is True
    assert signal.error is None
    assert signal.suggestion_count == 2
    assert signal.exact_phrase_is_suggestion is True
    assert signal.wikipedia_monthly_views == 12_500
    assert signal.wikipedia_article_title is not None
    assert signal.verdict == "growing"
    assert signal.source == "autocomplete+wikipedia"
    assert signal.search_volume is None


@pytest.mark.unit
async def test_no_suggestions_yields_flat_verdict() -> None:
    """No autocomplete suggestions AND no Wikipedia article → 'flat'."""
    keyword = "obscure fictional widget xyz"
    autocomplete = httpx.Response(200, text=_autocomplete_body(keyword, []))
    wikipedia = httpx.Response(404, json={"type": "not_found"})

    handler = _route_handler(autocomplete=autocomplete, wikipedia=wikipedia)
    with _patch_client(handler):
        signal = await check_keyword_async(keyword)

    assert signal.checked is True
    assert signal.suggestion_count == 0
    assert signal.autocomplete_suggestions == ()
    assert signal.wikipedia_monthly_views is None
    assert signal.wikipedia_article_title is None
    assert signal.verdict == "flat"


@pytest.mark.unit
async def test_suggestions_but_no_wiki_article_with_phrase_absent() -> None:
    """Suggestions exist, phrase NOT in them, no Wikipedia → 'declining'."""
    keyword = "thisphrase notin suggestions"
    # Suggestions that do NOT contain the keyword as a substring.
    autocomplete = httpx.Response(
        200, text=_autocomplete_body(keyword, ["unrelated one", "unrelated two"])
    )
    wikipedia = httpx.Response(404, json={"type": "not_found"})

    handler = _route_handler(autocomplete=autocomplete, wikipedia=wikipedia)
    with _patch_client(handler):
        signal = await check_keyword_async(keyword)

    assert signal.checked is True
    assert signal.exact_phrase_is_suggestion is False
    assert signal.wikipedia_monthly_views is None
    assert signal.verdict == "declining"


@pytest.mark.unit
async def test_wiki_breakout_tier() -> None:
    """>100K monthly views with phrase in suggestions → 'breakout'."""
    keyword = "meditation"
    autocomplete = httpx.Response(
        200, text=_autocomplete_body(keyword, ["meditation app", "meditation music"])
    )
    wikipedia = httpx.Response(200, json=_wikipedia_body([500_000]))

    handler = _route_handler(autocomplete=autocomplete, wikipedia=wikipedia)
    with _patch_client(handler):
        signal = await check_keyword_async(keyword)

    assert signal.checked is True
    assert signal.wikipedia_monthly_views == 500_000
    assert signal.verdict == "breakout"


@pytest.mark.unit
async def test_both_apis_500_yields_unchecked() -> None:
    """Autocomplete AND Wikipedia both 500 → 'unchecked', checked=False."""
    autocomplete = httpx.Response(500, text="server error")
    wikipedia = httpx.Response(500, json={"error": "boom"})

    handler = _route_handler(autocomplete=autocomplete, wikipedia=wikipedia)
    with _patch_client(handler):
        signal = await check_keyword_async("anything")

    assert signal.checked is False
    assert signal.verdict == "unchecked"
    assert signal.error is not None
    assert signal.suggestion_count == 0
    assert signal.wikipedia_monthly_views is None


@pytest.mark.unit
async def test_hyphenated_query_does_not_crash() -> None:
    """Previous pytrends failure case: queries with hyphens must not crash."""
    keyword = "ai-powered habit-tracker"
    autocomplete = httpx.Response(
        200,
        text=_autocomplete_body(keyword, ["ai-powered habit-tracker app"]),
    )
    wikipedia = httpx.Response(404, json={"type": "not_found"})

    handler = _route_handler(autocomplete=autocomplete, wikipedia=wikipedia)
    with _patch_client(handler):
        signal = await check_keyword_async(keyword)

    assert isinstance(signal, KeywordSignal)
    assert signal.checked is True
    # Should find phrase in suggestion.
    assert signal.exact_phrase_is_suggestion is True


@pytest.mark.unit
async def test_dataforseo_skipped_when_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """No DataForSEO env vars → source excludes 'dataforseo', volume None."""
    monkeypatch.delenv("DATAFORSEO_LOGIN", raising=False)
    monkeypatch.delenv("DATAFORSEO_PASSWORD", raising=False)

    keyword = "calorie counter"
    autocomplete = httpx.Response(
        200, text=_autocomplete_body(keyword, ["calorie counter app"])
    )
    wikipedia = httpx.Response(200, json=_wikipedia_body([2_000]))

    handler = _route_handler(autocomplete=autocomplete, wikipedia=wikipedia)
    with _patch_client(handler):
        signal = await check_keyword_async(keyword)

    assert signal.search_volume is None
    assert signal.cpc_usd is None
    assert "dataforseo" not in signal.source


@pytest.mark.unit
async def test_autocomplete_fails_wiki_succeeds() -> None:
    """Autocomplete hard fails, Wikipedia works → still checked, wiki drives verdict."""
    autocomplete = httpx.Response(500, text="down")
    wikipedia = httpx.Response(200, json=_wikipedia_body([40_000]))

    handler = _route_handler(autocomplete=autocomplete, wikipedia=wikipedia)
    with _patch_client(handler):
        signal = await check_keyword_async("yoga")

    assert signal.checked is True
    assert signal.autocomplete_suggestions == ()
    assert signal.wikipedia_monthly_views == 40_000


@pytest.mark.unit
def test_check_keyword_sync_matches_async_shape() -> None:
    """Sync wrapper and async fn return the same dataclass type + fields."""
    keyword = "calorie counter"
    autocomplete = httpx.Response(
        200, text=_autocomplete_body(keyword, ["calorie counter app"])
    )
    wikipedia = httpx.Response(200, json=_wikipedia_body([2_500]))

    handler = _route_handler(autocomplete=autocomplete, wikipedia=wikipedia)
    with _patch_client(handler):
        signal = check_keyword(keyword)

    assert isinstance(signal, KeywordSignal)
    assert signal.checked is True
    assert signal.keyword == keyword
    # Frozen dataclass — mutation attempts should fail.
    with pytest.raises(Exception):
        signal.verdict = "changed"  # type: ignore[misc]


@pytest.mark.unit
async def test_empty_keyword_is_rejected_without_network() -> None:
    """Empty keyword → unchecked immediately, no HTTP calls."""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(500)

    with _patch_client(handler):
        signal = await check_keyword_async("   ")

    assert signal.checked is False
    assert signal.verdict == "unchecked"
    assert signal.error == "empty keyword"
    assert call_count["n"] == 0
