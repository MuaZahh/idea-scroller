"""Keyword-volume validator — free two-tier pipeline.

Primary: Google Autocomplete. Secondary: Wikipedia pageviews REST API
(last 3 full months, summed). Optional fallback: DataForSEO
``search_volume/live`` (only when DATAFORSEO_LOGIN + DATAFORSEO_PASSWORD
are in env). Public API never raises — any error becomes
``checked=False, verdict='unchecked'``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)


VERDICT_BREAKOUT = "breakout"
VERDICT_GROWING = "growing"
VERDICT_STABLE = "stable"
VERDICT_DECLINING = "declining"
VERDICT_FLAT = "flat"
VERDICT_UNCHECKED = "unchecked"

SOURCE_AUTOCOMPLETE = "autocomplete"
SOURCE_AUTOCOMPLETE_WIKI = "autocomplete+wikipedia"
SOURCE_DATAFORSEO = "dataforseo"
SOURCE_UNCHECKED = "unchecked"

_USER_AGENT = "IdeaScroller/1.0"
_HTTP_TIMEOUT_SECONDS = 10.0
_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 1.0

_AUTOCOMPLETE_URL = "https://suggestqueries.google.com/complete/search"
_WIKIPEDIA_URL_TEMPLATE = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia/all-access/all-agents/{title}/monthly/{start}/{end}"
)
_DATAFORSEO_URL = (
    "https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live"
)

# (stable_floor, growing_floor, breakout_floor) — monthly counts.
_WIKI_FLOORS: tuple[int, int, int] = (1_000, 10_000, 100_000)
_DFS_FLOORS: tuple[int, int, int] = (1_000, 10_000, 100_000)


@dataclass(frozen=True)
class KeywordSignal:
    """Immutable summary of keyword popularity signals."""

    checked: bool
    error: str | None
    keyword: str
    autocomplete_suggestions: tuple[str, ...]
    suggestion_count: int
    exact_phrase_is_suggestion: bool
    wikipedia_monthly_views: int | None
    wikipedia_article_title: str | None
    search_volume: int | None
    cpc_usd: float | None
    source: str
    verdict: str


def _unchecked(keyword: str, error: str | None) -> KeywordSignal:
    """Build an 'unchecked' signal for a failed or skipped lookup."""
    return KeywordSignal(
        checked=False, error=error, keyword=keyword,
        autocomplete_suggestions=(), suggestion_count=0,
        exact_phrase_is_suggestion=False,
        wikipedia_monthly_views=None, wikipedia_article_title=None,
        search_volume=None, cpc_usd=None,
        source=SOURCE_UNCHECKED, verdict=VERDICT_UNCHECKED,
    )


async def _backoff(attempt: int) -> None:
    """Exponential backoff + jitter (base 1s, 2s, 4s)."""
    await asyncio.sleep(_BACKOFF_BASE_SECONDS * (2 ** attempt) + random.uniform(0.0, 0.5))


async def _get_with_retries(
    client: httpx.AsyncClient, url: str, *,
    params: dict[str, str] | None = None,
) -> httpx.Response | None:
    """GET with retries on 429 / transport errors. None on total failure."""
    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            if attempt < _MAX_RETRIES - 1:
                await _backoff(attempt)
                continue
            logger.warning("GET %s failed after retries: %s", url, exc)
            return None
        if response.status_code == 429 and attempt < _MAX_RETRIES - 1:
            logger.info("GET %s got 429 (attempt %d)", url, attempt)
            await _backoff(attempt)
            continue
        return response
    return None


def _parse_autocomplete_payload(raw: str) -> tuple[str, ...]:
    """Extract the suggestion list from a Google Autocomplete JSON array."""
    data = json.loads(raw)
    if not isinstance(data, list) or len(data) < 2 or not isinstance(data[1], list):
        return ()
    return tuple(
        str(s).strip() for s in data[1] if isinstance(s, str) and s.strip()
    )


async def _fetch_autocomplete(
    client: httpx.AsyncClient, keyword: str
) -> tuple[str, ...] | None:
    """Return suggestions tuple, or None on hard failure (5xx / transport)."""
    params = {"client": "firefox", "hl": "en", "gl": "us", "q": keyword}
    response = await _get_with_retries(client, _AUTOCOMPLETE_URL, params=params)
    if response is None:
        return None
    status = response.status_code
    if status >= 500:
        logger.info("Autocomplete %d for %r", status, keyword)
        return None
    if status >= 400:
        logger.info("Autocomplete %d for %r", status, keyword)
        return ()
    try:
        return _parse_autocomplete_payload(response.text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Autocomplete parse failed for %r: %s", keyword, exc)
        return None


def _candidate_wiki_titles(keyword: str) -> tuple[str, ...]:
    """Generate Wikipedia title candidates to try in order (de-duplicated).

    Also falls back to the last meaningful word in multi-word phrases, since
    Wikipedia has "Calorie" but not "calorie counter" — the broader concept
    is a reasonable proxy for keyword-volume signal.
    """
    stripped = keyword.strip()
    if not stripped:
        return ()
    words = stripped.split()
    raw: list[str] = [
        stripped.replace(" ", "_"),
        stripped.title().replace(" ", "_"),
        stripped.replace(" ", "-"),
    ]
    if len(words) > 1:
        # Last word (most specific noun usually comes last — "calorie counter" → "counter")
        raw.append(words[-1].title())
        # First word as concept fallback — "calorie counter" → "Calorie"
        raw.append(words[0].title())
    return tuple(dict.fromkeys(c for c in raw if c))


def _last_three_full_months() -> tuple[str, str]:
    """Return (start_YYYYMMDD, end_YYYYMMDD) spanning the last 3 months."""
    end_first = (date.today() - timedelta(days=1)).replace(day=1)
    year, month = end_first.year, end_first.month - 3
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1).strftime("%Y%m%d"), end_first.strftime("%Y%m%d")


def _sum_wiki_views(payload: Any) -> int | None:
    """Sum the ``views`` field across a Wikipedia ``items`` payload."""
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list) or not items:
        return None
    return sum(
        int(e["views"]) for e in items
        if isinstance(e, dict) and isinstance(e.get("views"), (int, float))
    )


async def _fetch_wikipedia_views(
    client: httpx.AsyncClient, keyword: str
) -> tuple[int | None, str | None, bool]:
    """Return (summed_views, matched_title, hard_failure). 404 is NOT failure."""
    start, end = _last_three_full_months()
    titles = _candidate_wiki_titles(keyword)
    if not titles:
        return None, None, False
    saw_miss = False
    saw_fail = False
    for title in titles:
        url = _WIKIPEDIA_URL_TEMPLATE.format(
            title=quote(title, safe=""), start=start, end=end
        )
        response = await _get_with_retries(client, url)
        if response is None:
            saw_fail = True
            continue
        status = response.status_code
        if status == 404 or (400 <= status < 500):
            saw_miss = True
            continue
        if status >= 500:
            saw_fail = True
            logger.info("Wikipedia %d for %r (%r)", status, title, keyword)
            continue
        try:
            total = _sum_wiki_views(response.json())
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Wikipedia JSON parse failed for %r: %s", title, exc)
            saw_fail = True
            continue
        if total is None:
            continue
        return total, title, False
    return None, None, saw_fail and not saw_miss


def _dataforseo_credentials() -> tuple[str, str] | None:
    """Return (login, password) if both env vars are set, else None."""
    login = os.environ.get("DATAFORSEO_LOGIN")
    password = os.environ.get("DATAFORSEO_PASSWORD")
    if login and password:
        return login, password
    return None


def _extract_dataforseo_fields(data: Any) -> tuple[int | None, float | None]:
    """Pull (search_volume, cpc) out of a DataForSEO v3 response body."""
    tasks = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(tasks, list) or not tasks or not isinstance(tasks[0], dict):
        return None, None
    result = tasks[0].get("result")
    if not isinstance(result, list) or not result or not isinstance(result[0], dict):
        return None, None
    v = result[0].get("search_volume")
    c = result[0].get("cpc")
    volume = int(v) if isinstance(v, (int, float)) else None
    cpc = float(c) if isinstance(c, (int, float)) else None
    return volume, cpc


async def _fetch_dataforseo(
    client: httpx.AsyncClient,
    keyword: str,
    credentials: tuple[str, str],
) -> tuple[int | None, float | None]:
    """Return (search_volume, cpc_usd). Both None on any failure."""
    login, password = credentials
    payload: list[dict[str, Any]] = [{"keywords": [keyword], "language_code": "en"}]
    try:
        response = await client.post(
            _DATAFORSEO_URL, json=payload, auth=(login, password)
        )
    except httpx.HTTPError as exc:
        logger.warning("DataForSEO request failed for %r: %s", keyword, exc)
        return None, None
    if response.status_code >= 400:
        logger.info("DataForSEO %d for %r", response.status_code, keyword)
        return None, None
    try:
        return _extract_dataforseo_fields(response.json())
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("DataForSEO JSON parse failed for %r: %s", keyword, exc)
        return None, None


def _classify_by_volume(volume: int, floors: tuple[int, int, int]) -> str:
    """Bucket a monthly volume number into a verdict label."""
    stable_floor, growing_floor, breakout_floor = floors
    if volume >= breakout_floor:
        return VERDICT_BREAKOUT
    if volume >= growing_floor:
        return VERDICT_GROWING
    if volume >= stable_floor:
        return VERDICT_STABLE
    return VERDICT_DECLINING


def _classify_verdict(
    *, suggestions: tuple[str, ...], exact_in: bool,
    wiki_views: int | None, dfs_volume: int | None,
) -> str:
    """Pick a verdict label from the available signals."""
    if dfs_volume is not None:
        return _classify_by_volume(dfs_volume, _DFS_FLOORS)
    if not suggestions and wiki_views is None:
        return VERDICT_FLAT
    if suggestions and not exact_in and (wiki_views is None or wiki_views < _WIKI_FLOORS[0]):
        return VERDICT_DECLINING
    if wiki_views is None:
        return VERDICT_STABLE if exact_in else VERDICT_DECLINING
    return _classify_by_volume(wiki_views, _WIKI_FLOORS)


async def check_keyword_async(keyword: str, *, timeout: int = 15) -> KeywordSignal:
    """Look up keyword popularity. Never raises."""
    if not isinstance(keyword, str) or not keyword.strip():
        return _unchecked(keyword if isinstance(keyword, str) else "", "empty keyword")
    keyword = keyword.strip()
    effective = float(min(max(timeout, 1), 60))
    headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(
            timeout=min(_HTTP_TIMEOUT_SECONDS, effective),
            headers=headers, follow_redirects=True,
        ) as client:
            raw = await _fetch_autocomplete(client, keyword)
            suggestions: tuple[str, ...] = raw or ()
            wiki_views, wiki_title, wiki_failed = await _fetch_wikipedia_views(client, keyword)
            dfs_volume: int | None = None
            dfs_cpc: float | None = None
            creds = _dataforseo_credentials()
            if creds is not None:
                dfs_volume, dfs_cpc = await _fetch_dataforseo(client, keyword, creds)
            if raw is None and wiki_failed and dfs_volume is None:
                return _unchecked(keyword, "all sources failed")
            lower = keyword.lower()
            # Hyphen/whitespace-insensitive match: "photo-based calorie counter"
            # should count as an exact match against suggestion "photo based calorie counter".
            def _norm(text: str) -> str:
                return " ".join(text.lower().replace("-", " ").split())
            norm_target = _norm(keyword)
            exact_in = any(norm_target in _norm(s) for s in suggestions)
            source = (
                SOURCE_DATAFORSEO if dfs_volume is not None
                else SOURCE_AUTOCOMPLETE_WIKI if wiki_title is not None
                else SOURCE_AUTOCOMPLETE
            )
            return KeywordSignal(
                checked=True, error=None, keyword=keyword,
                autocomplete_suggestions=suggestions,
                suggestion_count=len(suggestions),
                exact_phrase_is_suggestion=exact_in,
                wikipedia_monthly_views=wiki_views,
                wikipedia_article_title=wiki_title,
                search_volume=dfs_volume, cpc_usd=dfs_cpc, source=source,
                verdict=_classify_verdict(
                    suggestions=suggestions, exact_in=exact_in,
                    wiki_views=wiki_views, dfs_volume=dfs_volume,
                ),
            )
    except Exception as exc:  # noqa: BLE001 - public boundary; never raise
        logger.warning("check_keyword_async failed for %r: %s", keyword, exc)
        return _unchecked(keyword, f"{type(exc).__name__}: {exc}")


def check_keyword(keyword: str, *, timeout: int = 15) -> KeywordSignal:
    """Synchronous wrapper — mostly for tests. Runs the async function."""
    try:
        return asyncio.run(check_keyword_async(keyword, timeout=timeout))
    except RuntimeError as exc:
        logger.warning("check_keyword sync wrapper failed for %r: %s", keyword, exc)
        return _unchecked(keyword, f"sync-wrapper: {exc}")
