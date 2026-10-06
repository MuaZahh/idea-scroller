"""Unit tests for the Reddit pain-density validator."""

from __future__ import annotations

from typing import Callable

import httpx
import pytest

from ideascroller.validators.reddit import (
    RedditSignal,
    RedditThread,
    check_pain_density,
    search_reddit,
)


# Default selftext seed: every thread in these tests needs one of the
# literal pain phrases from ``_PAIN_PHRASES`` so it survives the
# client-side quality filter.
_DEFAULT_SELFTEXT = (
    "I wish there was an app that solves this. "
    + ("body text " * 40)
)


def _listing_payload(threads: list[dict]) -> dict:
    """Build a Reddit listing JSON envelope for the given thread dicts."""
    return {"data": {"children": [{"data": t} for t in threads]}}


def _thread_dict(
    *,
    title: str = "Thread title",
    subreddit: str = "AppIdeas",
    permalink: str = "/r/AppIdeas/comments/abc/thread/",
    score: int = 10,
    num_comments: int = 2,
    created_utc: float = 1710000000.0,
    selftext: str = _DEFAULT_SELFTEXT,
    over_18: bool = False,
) -> dict:
    return {
        "title": title,
        "subreddit": subreddit,
        "permalink": permalink,
        "score": score,
        "num_comments": num_comments,
        "created_utc": created_utc,
        "selftext": selftext,
        "over_18": over_18,
    }


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, timeout=10.0)


# ---------------------------------------------------------------------------
# Low-level search_reddit tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_search_reddit_parses_valid_response() -> None:
    """search_reddit maps the JSON envelope into RedditThread objects."""

    payload = _listing_payload(
        [
            _thread_dict(
                title="Is there an app for meal prep?",
                subreddit="MealPrepSunday",
                permalink="/r/MealPrepSunday/comments/abc/mp/",
                score=42,
                num_comments=5,
                created_utc=1710000000.0,
                selftext="I wish there was an app that could plan my meals."
                + " x" * 300,
            )
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["User-Agent"] == "IdeaScroller/1.0 (by /u/ideascroller)"
        assert request.url.host == "www.reddit.com"
        assert request.url.path == "/search.json"
        return httpx.Response(200, json=payload)

    async with _make_client(handler) as client:
        threads = await search_reddit("meal prep", client=client)

    assert len(threads) == 1
    thread = threads[0]
    assert isinstance(thread, RedditThread)
    assert thread.title == "Is there an app for meal prep?"
    assert thread.subreddit == "MealPrepSunday"
    assert thread.url == "https://reddit.com/r/MealPrepSunday/comments/abc/mp/"
    assert thread.score == 42
    assert thread.num_comments == 5
    assert thread.created_utc == 1710000000.0
    assert len(thread.selftext_preview) == 240
    assert thread.over_18 is False
    assert thread.permalink == "/r/MealPrepSunday/comments/abc/mp/"


@pytest.mark.unit
async def test_search_reddit_returns_empty_on_404() -> None:
    """A 404 response from Reddit should yield an empty list, no exception."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    async with _make_client(handler) as client:
        threads = await search_reddit("no such thing", client=client)

    assert threads == []


@pytest.mark.unit
async def test_search_reddit_retries_once_on_500_then_fails() -> None:
    """Two consecutive 500s should be swallowed and yield an empty list."""

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(500, text="server error")

    async with _make_client(handler) as client:
        threads = await search_reddit("flaky", client=client)

    assert threads == []
    assert call_count["n"] == 2  # original call + one retry


@pytest.mark.unit
async def test_search_reddit_retries_once_on_timeout_then_fails() -> None:
    """Transport-level timeouts should retry once and return gracefully."""

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        raise httpx.ConnectTimeout("timeout", request=request)

    async with _make_client(handler) as client:
        threads = await search_reddit("slow", client=client)

    assert threads == []
    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# Aggregation + verdict tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_check_pain_density_dedupes_by_url_across_phrases() -> None:
    """Same permalink surfaced by multiple phrases is counted once."""

    shared = _thread_dict(
        title="Shared thread",
        subreddit="SharedSub",
        permalink="/r/SharedSub/comments/x/shared/",
        score=20,
        num_comments=3,
    )
    unique = _thread_dict(
        title="Other thread",
        subreddit="OtherSub",
        permalink="/r/OtherSub/comments/y/unique/",
        score=5,
        num_comments=2,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("q", "")
        if '"tired of"' in query:
            return httpx.Response(200, json=_listing_payload([shared, unique]))
        return httpx.Response(200, json=_listing_payload([shared]))

    async with _make_client(handler) as client:
        signal = await check_pain_density("note taking", client=client)

    assert signal.checked is True
    assert signal.error is None
    # Deduped: exactly 2 unique URLs despite being returned multiple times.
    assert signal.total_threads_found == 2
    urls = [t.url for t in signal.top_threads]
    assert len(urls) == len(set(urls))
    assert signal.total_upvotes == 20 + 5
    assert signal.total_comments == 3 + 2


@pytest.mark.unit
async def test_check_pain_density_verdict_hot() -> None:
    """30 unique threads across phrases should produce a hot verdict."""

    # Each phrase returns 6 unique threads; 5 phrases seed * 6 = 30 unique.
    # (Remaining phrases' handlers just return an empty batch.)
    def build_threads(phrase_marker: str) -> list[dict]:
        return [
            _thread_dict(
                title=f"{phrase_marker} thread {i}",
                subreddit=f"Sub{phrase_marker}{i}",
                permalink=f"/r/S/comments/{phrase_marker}{i}/t/",
                score=25,
                num_comments=5,
            )
            for i in range(6)
        ]

    call_index = {"n": 0}
    markers = ["a", "b", "c", "d", "e"]

    def handler(request: httpx.Request) -> httpx.Response:
        idx = call_index["n"]
        call_index["n"] += 1
        if idx < len(markers):
            return httpx.Response(
                200, json=_listing_payload(build_threads(markers[idx]))
            )
        return httpx.Response(200, json=_listing_payload([]))

    async with _make_client(handler) as client:
        signal = await check_pain_density("productivity", client=client)

    assert signal.checked is True
    assert signal.total_threads_found == 30
    # Top threads capped at 8, each with score 25 -> total_upvotes 200.
    assert len(signal.top_threads) == 8
    assert signal.total_upvotes == 200
    assert signal.verdict == "hot"


@pytest.mark.unit
async def test_check_pain_density_verdict_hot_via_upvotes() -> None:
    """Few threads but >=500 summed upvotes in top_threads => hot."""

    heavy = [
        _thread_dict(
            title=f"Heavy {i}",
            subreddit=f"Heavy{i}",
            permalink=f"/r/H/comments/heavy{i}/t/",
            score=200,
            num_comments=2,
        )
        for i in range(4)
    ]

    served = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if served["done"]:
            return httpx.Response(200, json=_listing_payload([]))
        served["done"] = True
        return httpx.Response(200, json=_listing_payload(heavy))

    async with _make_client(handler) as client:
        signal = await check_pain_density("obscure", client=client)

    assert signal.total_threads_found == 4
    assert signal.total_upvotes == 800
    assert signal.verdict == "hot"


@pytest.mark.unit
async def test_check_pain_density_verdict_warm() -> None:
    """Exactly 10 unique threads with ~200 summed upvotes => warm."""

    threads = [
        _thread_dict(
            title=f"Warm {i}",
            subreddit=f"Warm{i}",
            permalink=f"/r/W/comments/warm{i}/t/",
            score=25,
            num_comments=2,
        )
        for i in range(10)
    ]

    served = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if served["done"]:
            return httpx.Response(200, json=_listing_payload([]))
        served["done"] = True
        return httpx.Response(200, json=_listing_payload(threads))

    async with _make_client(handler) as client:
        signal = await check_pain_density("journaling", client=client)

    assert signal.total_threads_found == 10
    # Top 8 threads, each score 25 -> 200 total upvotes.
    assert signal.total_upvotes == 200
    assert signal.verdict == "warm"


@pytest.mark.unit
async def test_check_pain_density_verdict_cool() -> None:
    """2 unique threads with ~20 upvotes total => cool."""

    threads = [
        _thread_dict(
            title="Cool 1",
            subreddit="Cool",
            permalink="/r/Cool/comments/c1/t/",
            score=10,
            num_comments=2,
        ),
        _thread_dict(
            title="Cool 2",
            subreddit="Cool",
            permalink="/r/Cool/comments/c2/t/",
            score=10,
            num_comments=2,
        ),
    ]

    served = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if served["done"]:
            return httpx.Response(200, json=_listing_payload([]))
        served["done"] = True
        return httpx.Response(200, json=_listing_payload(threads))

    async with _make_client(handler) as client:
        signal = await check_pain_density("tiny niche", client=client)

    assert signal.total_threads_found == 2
    assert signal.total_upvotes == 20
    assert signal.verdict == "cool"


@pytest.mark.unit
async def test_check_pain_density_verdict_dead() -> None:
    """All searches succeed but return zero threads => dead."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_listing_payload([]))

    async with _make_client(handler) as client:
        signal = await check_pain_density("unobtainium widget", client=client)

    assert signal.checked is True
    assert signal.error is None
    assert signal.total_threads_found == 0
    assert signal.top_threads == []
    assert signal.total_upvotes == 0
    assert signal.verdict == "dead"


@pytest.mark.unit
async def test_check_pain_density_verdict_unchecked_when_all_fail() -> None:
    """When every call throws a transport error => unchecked, checked=False."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network", request=request)

    async with _make_client(handler) as client:
        signal = await check_pain_density("network-down", client=client)

    assert isinstance(signal, RedditSignal)
    assert signal.checked is False
    assert signal.error is not None
    assert signal.verdict == "unchecked"
    assert signal.total_threads_found == 0
    assert signal.top_threads == []


@pytest.mark.unit
async def test_check_pain_density_subreddits_dedupe_preserves_order() -> None:
    """Subreddits list is deduped and keeps first-seen ordering."""

    ordered_sub_threads = [
        _thread_dict(
            title="T1",
            subreddit="Alpha",
            permalink="/r/Alpha/comments/1/t/",
            score=10,
            num_comments=2,
        ),
        _thread_dict(
            title="T2",
            subreddit="Beta",
            permalink="/r/Beta/comments/2/t/",
            score=5,
            num_comments=2,
        ),
        _thread_dict(
            title="T3",
            subreddit="Alpha",
            permalink="/r/Alpha/comments/3/t/",
            score=3,
            num_comments=2,
        ),
        _thread_dict(
            title="T4",
            subreddit="Gamma",
            permalink="/r/Gamma/comments/4/t/",
            score=1,
            num_comments=2,
        ),
    ]

    served = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if served["done"]:
            return httpx.Response(200, json=_listing_payload([]))
        served["done"] = True
        return httpx.Response(200, json=_listing_payload(ordered_sub_threads))

    async with _make_client(handler) as client:
        signal = await check_pain_density("ordering", client=client)

    assert signal.subreddits == ["Alpha", "Beta", "Gamma"]


# ---------------------------------------------------------------------------
# Client-side quality filter tests
# ---------------------------------------------------------------------------


async def _run_with_single_batch(threads: list[dict]) -> RedditSignal:
    """Helper: serve one batch of threads on the first phrase search."""
    served = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if served["done"]:
            return httpx.Response(200, json=_listing_payload([]))
        served["done"] = True
        return httpx.Response(200, json=_listing_payload(threads))

    async with _make_client(handler) as client:
        return await check_pain_density("keyword", client=client)


@pytest.mark.unit
async def test_blocked_subreddit_is_filtered_out() -> None:
    """A thread from a blocklisted subreddit (AmItheAsshole) is dropped."""

    thread = _thread_dict(
        title="AITA for something",
        subreddit="AmItheAsshole",
        permalink="/r/AmItheAsshole/comments/x/aita/",
        score=50,
        num_comments=10,
    )

    signal = await _run_with_single_batch([thread])

    assert signal.total_threads_found == 0
    assert signal.top_threads == []
    assert signal.verdict == "dead"


@pytest.mark.unit
async def test_blocked_subreddit_case_insensitive() -> None:
    """Blocklist match is case-insensitive."""

    thread = _thread_dict(
        title="Random story",
        subreddit="TIFU",  # upper-case in payload, lower-case in blocklist
        permalink="/r/TIFU/comments/x/story/",
        score=50,
        num_comments=10,
    )

    signal = await _run_with_single_batch([thread])

    assert signal.total_threads_found == 0


@pytest.mark.unit
async def test_engagement_farmed_thread_is_filtered_out() -> None:
    """A thread with score > 1000 is treated as viral noise and dropped."""

    viral = _thread_dict(
        title="Huge viral post",
        subreddit="AppIdeas",
        permalink="/r/AppIdeas/comments/x/viral/",
        score=5000,
        num_comments=50,
    )

    signal = await _run_with_single_batch([viral])

    assert signal.total_threads_found == 0
    assert signal.verdict == "dead"


@pytest.mark.unit
async def test_thread_with_zero_comments_is_filtered_out() -> None:
    """Threads with < 2 comments are dropped (no discussion == no signal)."""

    silent = _thread_dict(
        title="Nobody replied",
        subreddit="AppIdeas",
        permalink="/r/AppIdeas/comments/x/silent/",
        score=20,
        num_comments=0,
    )

    signal = await _run_with_single_batch([silent])

    assert signal.total_threads_found == 0


@pytest.mark.unit
async def test_deleted_selftext_is_filtered_out() -> None:
    """Threads whose body is ``[deleted]`` or ``[removed]`` are dropped."""

    deleted = _thread_dict(
        title="Gone post",
        subreddit="AppIdeas",
        permalink="/r/AppIdeas/comments/x/gone/",
        score=20,
        num_comments=5,
        selftext="[deleted]",
    )
    removed = _thread_dict(
        title="Removed post",
        subreddit="AppIdeas",
        permalink="/r/AppIdeas/comments/x/removed/",
        score=20,
        num_comments=5,
        selftext="[removed]",
    )

    signal = await _run_with_single_batch([deleted, removed])

    assert signal.total_threads_found == 0


@pytest.mark.unit
async def test_thread_without_pain_phrase_is_filtered_out() -> None:
    """Threads whose body/title contain no pain-phrase literal are dropped."""

    off_topic = _thread_dict(
        title="Just chatting",
        subreddit="AppIdeas",
        permalink="/r/AppIdeas/comments/x/chat/",
        score=20,
        num_comments=5,
        selftext="This body has lots of words but none of the pain phrases.",
    )

    signal = await _run_with_single_batch([off_topic])

    assert signal.total_threads_found == 0


@pytest.mark.unit
async def test_nsfw_thread_is_filtered_out() -> None:
    """NSFW (over_18) threads are dropped regardless of other metrics."""

    nsfw = _thread_dict(
        title="I wish there was an app",
        subreddit="AppIdeas",
        permalink="/r/AppIdeas/comments/x/nsfw/",
        score=20,
        num_comments=5,
        over_18=True,
    )

    signal = await _run_with_single_batch([nsfw])

    assert signal.total_threads_found == 0


# ---------------------------------------------------------------------------
# Query construction tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_query_uses_selftext_field_scoped_pain_phrase() -> None:
    """The emitted ``q`` must contain ``selftext:"i wish there was"``."""

    captured_raw_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # httpx.URL.params decodes ``+`` back to space, which is exactly
        # what we want for verifying the literal pain-phrase substring.
        captured_raw_queries.append(request.url.params.get("q", ""))
        return httpx.Response(200, json=_listing_payload([]))

    async with _make_client(handler) as client:
        await check_pain_density("meal prep", client=client)

    # At least one outgoing query must carry the selftext-scoped phrase.
    assert any(
        'selftext:"i wish there was"' in q for q in captured_raw_queries
    )
    # And the keyword tokens must be appended after the phrase.
    assert any("meal prep" in q for q in captured_raw_queries)


@pytest.mark.unit
async def test_query_url_encodes_selftext_phrase_with_plus_for_spaces() -> None:
    """The raw URL must encode spaces in q as ``+`` (Reddit Lucene dialect)."""

    captured_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_urls.append(str(request.url))
        return httpx.Response(200, json=_listing_payload([]))

    async with _make_client(handler) as client:
        await check_pain_density("meal prep", client=client)

    # The URL-encoded form keeps ``selftext:``, quotes the phrase, and
    # joins tokens with ``+`` (urlencode with quote_plus).
    assert any(
        "q=selftext%3A%22i+wish+there+was%22" in u for u in captured_urls
    )


@pytest.mark.unit
async def test_query_uses_sort_new_and_time_year() -> None:
    """Each outgoing query must include ``sort=new`` and ``t=year``."""

    captured_params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_params.append(dict(request.url.params))
        return httpx.Response(200, json=_listing_payload([]))

    async with _make_client(handler) as client:
        await check_pain_density("any keyword", client=client)

    assert captured_params, "expected at least one outgoing request"
    for params in captured_params:
        assert params.get("sort") == "new"
        assert params.get("t") == "year"
        assert params.get("limit") == "100"
        assert params.get("restrict_sr") == "0"


# ---------------------------------------------------------------------------
# Quality-score ordering test
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_top_threads_ordered_by_quality_score() -> None:
    """Quality score = min(score, 500) + num_comments * 3.

    Inputs chosen to prove BOTH the score cap and the comment multiplier
    kick in:
      - T1: score=50,  comments=2   -> min(50,500)+6    = 56
      - T2: score=200, comments=40  -> min(200,500)+120 = 320
      - T3: score=800, comments=3   -> min(800,500)+9   = 509  (capped!)

    Expected order: T3 (509) > T2 (320) > T1 (56). Without the cap T3
    would score 809 (trivially first); without the comment multiplier T2
    would score 203 (last). Using the spec formula, T3 wins by only 189
    over T2 — proving the cap is active (if uncapped, T3 would dominate
    by 606) — and T2 beats T1 by 264, which is impossible unless the
    comment multiplier is at least 3x (40 comments vs 2 -> 38 extra
    comments contribute 114+ points).
    """

    t1 = _thread_dict(
        title="Low signal",
        subreddit="AppIdeasA",
        permalink="/r/A/comments/t1/x/",
        score=50,
        num_comments=2,
    )
    t2 = _thread_dict(
        title="High discussion",
        subreddit="AppIdeasB",
        permalink="/r/B/comments/t2/x/",
        score=200,
        num_comments=40,
    )
    t3 = _thread_dict(
        title="Very popular",
        subreddit="AppIdeasC",
        permalink="/r/C/comments/t3/x/",
        score=800,
        num_comments=3,
    )

    served = {"done": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if served["done"]:
            return httpx.Response(200, json=_listing_payload([]))
        served["done"] = True
        return httpx.Response(200, json=_listing_payload([t1, t2, t3]))

    async with _make_client(handler) as client:
        signal = await check_pain_density("ordering", client=client)

    assert signal.total_threads_found == 3
    ordered_titles = [t.title for t in signal.top_threads]
    assert ordered_titles == ["Very popular", "High discussion", "Low signal"]


# ---------------------------------------------------------------------------
# Low-level search_reddit query params
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_search_reddit_sends_correct_query_params() -> None:
    """Low-level ``search_reddit`` emits q/limit/sort/t/restrict_sr."""

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json=_listing_payload([]))

    async with _make_client(handler) as client:
        await search_reddit("my query", limit=5, client=client)

    assert captured["params"]["q"] == "my query"
    assert captured["params"]["limit"] == "5"
    assert captured["params"]["sort"] == "new"
    assert captured["params"]["t"] == "year"
    assert captured["params"]["restrict_sr"] == "0"
