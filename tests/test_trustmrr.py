"""Unit tests for the TrustMRR validator."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest

from ideascroller.validators import trustmrr as trustmrr_module
from ideascroller.validators.trustmrr import (
    TRUSTMRR_BASE_URL,
    FuzzyMatch,
    TrustMRRCompetitor,
    fetch_top_startups,
    is_cache_fresh,
    load_cached_corpus,
    match_competitors,
    sync_corpus,
    validate_category,
    validate_idea,
)


# --- Helpers --------------------------------------------------------------


def _startup_record(
    *,
    name: str,
    slug: str,
    mrr_cents: int,
    total_cents: int = 0,
    customers: int = 0,
    growth: float = 0.0,
    category: str = "saas",
) -> dict[str, Any]:
    """Build a TrustMRR-shaped startup record (monetary values in cents)."""
    return {
        "name": name,
        "slug": slug,
        "description": f"{name} description",
        "website": f"https://{slug}.com",
        "category": category,
        "revenue": {
            "mrr": mrr_cents,
            "last30Days": mrr_cents,
            "total": total_cents,
        },
        "customers": customers,
        "activeSubscriptions": customers,
        "growth30d": growth,
        "growthMRR30d": growth,
        "rank": 1,
    }


def _json_response(records: list[dict[str, Any]], status: int = 200) -> httpx.Response:
    """Build a JSON response mimicking the TrustMRR envelope."""
    body = json.dumps({"data": records, "meta": {"total": len(records)}})
    return httpx.Response(
        status_code=status,
        content=body.encode("utf-8"),
        headers={"content-type": "application/json"},
    )


def _make_client(handler) -> httpx.AsyncClient:
    """Create an AsyncClient backed by a MockTransport handler."""
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, timeout=10.0)


# --- fetch_top_startups ---------------------------------------------------


@pytest.mark.unit
async def test_fetch_top_startups_parses_and_converts_cents() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        records = [
            _startup_record(
                name="Alpha", slug="alpha", mrr_cents=5_000_000,
                total_cents=60_000_000, customers=120, growth=0.12,
            ),
            _startup_record(
                name="Beta", slug="beta", mrr_cents=1_500_000,
                total_cents=5_000_000, customers=40, growth=0.05,
            ),
        ]
        return _json_response(records)

    async with _make_client(handler) as client:
        results = await fetch_top_startups(
            "tmrr_testkey", "saas", limit=5, client=client,
        )

    assert captured["auth"] == "Bearer tmrr_testkey"
    assert f"{TRUSTMRR_BASE_URL}/startups" in captured["url"]
    assert "category=saas" in captured["url"]
    assert "sort=revenue-desc" in captured["url"]
    assert "limit=5" in captured["url"]

    assert len(results) == 2
    assert all(isinstance(r, TrustMRRCompetitor) for r in results)
    # Sorted by MRR desc
    assert results[0].name == "Alpha"
    assert results[1].name == "Beta"
    # Cents -> dollars
    assert results[0].mrr_usd == pytest.approx(50_000.0)
    assert results[0].total_revenue_usd == pytest.approx(600_000.0)
    assert results[1].mrr_usd == pytest.approx(15_000.0)
    assert results[0].customers == 120
    assert results[0].growth_mrr_30d == pytest.approx(0.12)
    assert results[0].url == "https://trustmrr.com/startups/alpha"


@pytest.mark.unit
async def test_fetch_top_startups_passes_min_revenue_cents() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return _json_response([])

    async with _make_client(handler) as client:
        await fetch_top_startups(
            "tmrr_testkey", "ai", limit=3, min_revenue_cents=1_000_000,
            client=client,
        )

    assert "minRevenue=1000000" in captured["url"]
    assert "limit=3" in captured["url"]


# --- validate_category: verdict cases -------------------------------------


@pytest.mark.unit
async def test_validate_category_strong_when_mrr_above_100k() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response([
            _startup_record(name="BigCo", slug="bigco", mrr_cents=10_000_000),
            _startup_record(name="SmallCo", slug="smallco", mrr_cents=500_000),
        ])

    async with _make_client(handler) as client:
        signal = await validate_category("tmrr_testkey", "ai", client=client)

    assert signal.checked is True
    assert signal.error is None
    assert signal.verdict == "strong"
    assert signal.max_mrr_usd == pytest.approx(100_000.0)
    assert signal.competitors_above_100k_mrr == 1
    assert signal.competitors_above_10k_mrr == 1
    assert len(signal.top_competitors) == 2
    assert signal.top_competitors[0].name == "BigCo"


@pytest.mark.unit
async def test_validate_category_viable_when_max_is_15k() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response([
            _startup_record(name="MidCo", slug="midco", mrr_cents=1_500_000),
            _startup_record(name="TinyCo", slug="tinyco", mrr_cents=10_000),
        ])

    async with _make_client(handler) as client:
        signal = await validate_category("tmrr_testkey", "saas", client=client)

    assert signal.checked is True
    assert signal.verdict == "viable"
    assert signal.max_mrr_usd == pytest.approx(15_000.0)
    assert signal.competitors_above_100k_mrr == 0
    assert signal.competitors_above_10k_mrr == 1


@pytest.mark.unit
async def test_validate_category_weak_when_mrr_positive_but_below_10k() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response([
            _startup_record(name="Seedling", slug="seedling", mrr_cents=50_000),
        ])

    async with _make_client(handler) as client:
        signal = await validate_category("tmrr_testkey", "niche", client=client)

    assert signal.checked is True
    assert signal.verdict == "weak"
    assert signal.max_mrr_usd == pytest.approx(500.0)


@pytest.mark.unit
async def test_validate_category_absent_on_empty_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response([])

    async with _make_client(handler) as client:
        signal = await validate_category("tmrr_testkey", "empty", client=client)

    assert signal.checked is True
    assert signal.error is None
    assert signal.verdict == "absent"
    assert signal.max_mrr_usd == 0.0
    assert signal.top_competitors == []


# --- validate_category: unchecked/error cases ----------------------------


@pytest.mark.unit
async def test_validate_category_unchecked_when_no_api_key() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return _json_response([])

    async with _make_client(handler) as client:
        signal = await validate_category(None, "ai", client=client)

    assert called is False, "no HTTP call should be made without an api_key"
    assert signal.checked is False
    assert signal.error is None
    assert signal.verdict == "unchecked"
    assert signal.top_competitors == []


@pytest.mark.unit
async def test_validate_category_unchecked_on_http_500() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=500,
            content=b'{"error":"internal"}',
            headers={"content-type": "application/json"},
        )

    async with _make_client(handler) as client:
        signal = await validate_category("tmrr_testkey", "ai", client=client)

    assert signal.checked is False
    assert signal.verdict == "unchecked"
    assert signal.error is not None
    assert "500" in signal.error


@pytest.mark.unit
async def test_validate_category_unchecked_on_network_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("simulated timeout", request=request)

    async with _make_client(handler) as client:
        signal = await validate_category("tmrr_testkey", "ai", client=client)

    assert signal.checked is False
    assert signal.verdict == "unchecked"
    assert signal.error is not None
    assert "timeout" in signal.error.lower()


# --- Rate-limiting sanity check -------------------------------------------


@pytest.mark.unit
async def test_validate_category_handles_concurrent_calls_without_error() -> None:
    """25 concurrent calls must all complete; the semaphore must serialize them safely."""
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return _json_response([
            _startup_record(name="Co", slug="co", mrr_cents=2_000_000),
        ])

    async with _make_client(handler) as client:
        results = await asyncio.gather(
            *(validate_category("tmrr_testkey", "saas", client=client) for _ in range(25))
        )

    assert len(results) == 25
    assert all(r.checked for r in results)
    assert all(r.verdict == "viable" for r in results)
    assert call_count == 25


# --- Fixtures for corpus cache + fuzzy tests ------------------------------


@pytest.fixture
def isolated_cache(monkeypatch, tmp_path: Path) -> Path:
    """Redirect the TrustMRR cache dir to a tmp_path for each test."""
    cache_dir = tmp_path / "trustmrr_cache"
    monkeypatch.setattr(trustmrr_module, "_CACHE_DIR", cache_dir)
    return cache_dir


def _paginated_handler(pages: list[list[dict[str, Any]]]):
    """Handler that returns each page in order, setting meta.hasMore correctly."""
    captured_pages: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page_str = request.url.params.get("page", "1")
        page = int(page_str)
        captured_pages.append(page)
        idx = page - 1
        if idx >= len(pages):
            body = json.dumps({"data": [], "meta": {"hasMore": False}})
        else:
            has_more = idx < len(pages) - 1
            body = json.dumps({"data": pages[idx], "meta": {"hasMore": has_more}})
        return httpx.Response(
            status_code=200, content=body.encode("utf-8"),
            headers={"content-type": "application/json"},
        )
    return handler, captured_pages


# --- sync_corpus ----------------------------------------------------------


@pytest.mark.unit
async def test_sync_corpus_paginates_until_has_more_false(isolated_cache: Path) -> None:
    page1 = [_startup_record(name=f"p1-{i}", slug=f"p1-{i}", mrr_cents=100_000)
             for i in range(3)]
    page2 = [_startup_record(name=f"p2-{i}", slug=f"p2-{i}", mrr_cents=100_000)
             for i in range(2)]
    handler, pages_seen = _paginated_handler([page1, page2])

    async with _make_client(handler) as client:
        records = await sync_corpus("tmrr_key", force=True, client=client)

    assert pages_seen == [1, 2]
    assert len(records) == 5
    corpus_path = isolated_cache / "trustmrr_corpus.jsonl"
    synced_path = isolated_cache / "trustmrr_corpus.synced_at"
    assert corpus_path.exists()
    assert synced_path.exists()
    lines = [ln for ln in corpus_path.read_text().splitlines() if ln.strip()]
    assert len(lines) == 5
    # synced_at must be a parseable ISO timestamp.
    datetime.fromisoformat(synced_path.read_text().strip())


@pytest.mark.unit
async def test_sync_corpus_stops_on_first_has_more_false(isolated_cache: Path) -> None:
    """A single page with hasMore=False must not trigger a second request."""
    handler, pages_seen = _paginated_handler([
        [_startup_record(name="only", slug="only", mrr_cents=500_000)],
    ])
    async with _make_client(handler) as client:
        records = await sync_corpus("tmrr_key", force=True, client=client)
    assert pages_seen == [1]
    assert len(records) == 1


# --- load_cached_corpus + is_cache_fresh ----------------------------------


@pytest.mark.unit
def test_load_cached_corpus_empty_when_missing(isolated_cache: Path) -> None:
    assert load_cached_corpus() == []


@pytest.mark.unit
def test_load_cached_corpus_returns_full_list(isolated_cache: Path) -> None:
    isolated_cache.mkdir(parents=True, exist_ok=True)
    corpus_path = isolated_cache / "trustmrr_corpus.jsonl"
    records = [
        {"name": "A", "slug": "a", "category": "ai"},
        {"name": "B", "slug": "b", "category": "saas"},
    ]
    corpus_path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    loaded = load_cached_corpus()
    assert len(loaded) == 2
    assert loaded[0]["slug"] == "a"
    assert loaded[1]["slug"] == "b"


@pytest.mark.unit
def test_is_cache_fresh_false_when_25h_old(isolated_cache: Path) -> None:
    isolated_cache.mkdir(parents=True, exist_ok=True)
    stale = datetime.now(tz=timezone.utc) - timedelta(hours=25)
    (isolated_cache / "trustmrr_corpus.synced_at").write_text(stale.isoformat())
    assert is_cache_fresh() is False


@pytest.mark.unit
def test_is_cache_fresh_true_when_23h_old(isolated_cache: Path) -> None:
    isolated_cache.mkdir(parents=True, exist_ok=True)
    fresh = datetime.now(tz=timezone.utc) - timedelta(hours=23)
    (isolated_cache / "trustmrr_corpus.synced_at").write_text(fresh.isoformat())
    assert is_cache_fresh() is True


@pytest.mark.unit
def test_is_cache_fresh_false_when_missing(isolated_cache: Path) -> None:
    assert is_cache_fresh() is False


# --- match_competitors ----------------------------------------------------


@pytest.mark.unit
def test_match_competitors_empty_on_no_matches() -> None:
    corpus = [
        _startup_record(name="Zebra Accounting", slug="zebra",
                        mrr_cents=100_000, category="fintech"),
        _startup_record(name="Qwerty Ledger", slug="qwerty",
                        mrr_cents=100_000, category="fintech"),
    ]
    # Query has zero lexical overlap with either startup.
    matches = match_competitors("quantum flux capacitor", corpus, min_score=90)
    assert matches == []


@pytest.mark.unit
def test_match_competitors_category_boost_reorders_results() -> None:
    """A weaker-named competitor in the boosted category should outrank a stronger-named one."""
    strong_ai = _startup_record(
        name="Calorie Counter", slug="calorie-counter-ai",
        mrr_cents=50_000, category="ai",
    )
    weaker_health = _startup_record(
        name="Calorie Count App", slug="calorie-count-app",
        mrr_cents=50_000, category="health-fitness",
    )
    corpus = [strong_ai, weaker_health]

    # Without any boost: the stronger-named "Calorie Counter" wins.
    unboosted = match_competitors("calorie counter", corpus, min_score=0)
    assert unboosted[0].competitor.slug == "calorie-counter-ai"

    # With the health-fitness boost: +10 is enough to flip the order.
    boosted = match_competitors(
        "calorie counter", corpus, min_score=0, category_boost=("health-fitness",),
    )
    assert len(boosted) == 2
    assert boosted[0].competitor.slug == "calorie-count-app", (
        "health-fitness boost must lift the weaker name above a stronger un-boosted one"
    )


@pytest.mark.unit
def test_match_competitors_realistic_rezi_vs_macrofactor() -> None:
    rezi = _startup_record(
        name="Rezi", slug="rezi",
        mrr_cents=5_000_000, category="productivity",
    )
    rezi["description"] = "AI resume builder that gets your resume past ATS filters."
    macrofactor = _startup_record(
        name="MacroFactor", slug="macrofactor",
        mrr_cents=8_000_000, category="health-fitness",
    )
    macrofactor["description"] = (
        "AI calorie counter and macro tracker with an expert-designed nutrition coach."
    )
    matches = match_competitors(
        "AI calorie counter", [rezi, macrofactor],
        min_score=0, category_boost=("health-fitness",),
    )
    assert matches, "expected at least one match"
    assert matches[0].competitor.slug == "macrofactor"


@pytest.mark.unit
def test_match_competitors_filters_below_min_score() -> None:
    corpus = [
        _startup_record(name="Exact Match Co", slug="exact",
                        mrr_cents=100_000, category="ai"),
        _startup_record(name="Totally Unrelated Thing", slug="unrelated",
                        mrr_cents=100_000, category="ai"),
    ]
    matches = match_competitors("exact match", corpus, min_score=70)
    assert all(isinstance(m, FuzzyMatch) for m in matches)
    assert any(m.competitor.slug == "exact" for m in matches)


@pytest.mark.unit
def test_match_competitors_empty_query_returns_empty() -> None:
    corpus = [_startup_record(name="A", slug="a", mrr_cents=100_000)]
    assert match_competitors("", corpus) == []
    assert match_competitors("   ", corpus) == []


@pytest.mark.unit
def test_match_competitors_empty_corpus_returns_empty() -> None:
    assert match_competitors("anything", []) == []


# --- validate_idea --------------------------------------------------------


@pytest.mark.unit
async def test_validate_idea_no_api_key_is_unchecked(isolated_cache: Path) -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return _json_response([])

    async with _make_client(handler) as client:
        signal = await validate_idea(
            None, theme="ai", app_idea="calorie counter",
            category_hint="health-fitness", client=client,
        )
    assert called is False
    assert signal.checked is False
    assert signal.verdict == "unchecked"
    assert signal.verdict_source == "unchecked"


@pytest.mark.unit
async def test_validate_idea_fuzzy_match_produces_fuzzy_verdict_source(
    isolated_cache: Path,
) -> None:
    """Seeded fresh cache with strong fuzzy match → verdict_source='fuzzy_match'."""
    isolated_cache.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "name": "MacroFactor", "slug": "macrofactor",
            "description": "AI calorie counter and macro tracker for fitness.",
            "category": "health-fitness",
            "revenue": {"mrr": 8_000_000, "total": 200_000_000},
        },
        {
            "name": "Rezi", "slug": "rezi",
            "description": "AI resume builder for ATS filters.",
            "category": "productivity",
            "revenue": {"mrr": 5_000_000, "total": 100_000_000},
        },
    ]
    (isolated_cache / "trustmrr_corpus.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n"
    )
    fresh = datetime.now(tz=timezone.utc)
    (isolated_cache / "trustmrr_corpus.synced_at").write_text(fresh.isoformat())

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail(f"Cache was fresh — no HTTP call expected, got {request.url}")

    async with _make_client(handler) as client:
        signal = await validate_idea(
            "tmrr_testkey",
            theme="fitness",
            app_idea="ai calorie counter",
            category_hint="health-fitness",
            client=client,
        )

    assert signal.checked is True
    assert signal.verdict_source == "fuzzy_match"
    assert signal.top_competitors, "expected matched competitors"
    slugs = {c.slug for c in signal.top_competitors}
    assert "macrofactor" in slugs
    assert signal.fuzzy_scores, "fuzzy_scores should be populated"


@pytest.mark.unit
async def test_validate_idea_falls_back_to_category_on_empty_corpus(
    monkeypatch, isolated_cache: Path,
) -> None:
    """Empty corpus + API key → fuzzy has no hits → category fallback is tagged."""
    # Mock sync_corpus so we don't need pagination responses.
    async def fake_sync(api_key: str, *, force: bool = False,
                        client: Any = None) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(trustmrr_module, "sync_corpus", fake_sync)

    category_call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal category_call_count
        category_call_count += 1
        # /startups with category filter — emulate the fallback response.
        assert request.url.params.get("category") == "health-fitness"
        return _json_response([
            _startup_record(
                name="SomeFitnessApp", slug="some-fitness",
                mrr_cents=2_000_000, category="health-fitness",
            ),
        ])

    async with _make_client(handler) as client:
        signal = await validate_idea(
            "tmrr_testkey",
            theme="fitness",
            app_idea="calorie counter",
            category_hint="health-fitness",
            client=client,
        )

    assert category_call_count == 1
    assert signal.verdict_source == "category_fallback"
    assert signal.checked is True
    # Fallback returned the category-filtered competitor.
    assert any(c.slug == "some-fitness" for c in signal.top_competitors)


@pytest.mark.unit
async def test_validate_idea_stale_cache_triggers_sync(
    monkeypatch, isolated_cache: Path,
) -> None:
    """Stale cache → sync_corpus must be invoked."""
    isolated_cache.mkdir(parents=True, exist_ok=True)
    # Pre-seed an old synced_at file but no corpus → is_cache_fresh → False.
    stale = datetime.now(tz=timezone.utc) - timedelta(hours=48)
    (isolated_cache / "trustmrr_corpus.synced_at").write_text(stale.isoformat())

    sync_calls: list[str] = []

    async def fake_sync(api_key: str, *, force: bool = False,
                        client: Any = None) -> list[dict[str, Any]]:
        sync_calls.append(api_key)
        # Return a fresh corpus containing a strong match for the query.
        return [
            {
                "name": "FitTracker Pro", "slug": "fittracker",
                "description": "AI-powered calorie counter for fitness nerds.",
                "category": "health-fitness",
                "revenue": {"mrr": 6_000_000, "total": 120_000_000},
            },
        ]

    monkeypatch.setattr(trustmrr_module, "sync_corpus", fake_sync)

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("sync_corpus is mocked — no real HTTP should occur")

    async with _make_client(handler) as client:
        signal = await validate_idea(
            "tmrr_testkey",
            theme="fitness",
            app_idea="ai calorie counter",
            category_hint="health-fitness",
            client=client,
        )

    assert sync_calls == ["tmrr_testkey"], "stale cache must trigger exactly one sync call"
    assert signal.verdict_source == "fuzzy_match"
    assert any(c.slug == "fittracker" for c in signal.top_competitors)
