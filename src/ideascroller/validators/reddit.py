"""Reddit pain-density validator using public JSON endpoints.

Attaches Reddit thread evidence to surfaced app ideas by running a set of
field-scoped pain-phrase searches (e.g. ``selftext:"i wish there was"``)
against Reddit's unauthenticated search API, aggressively filtering out
viral/engagement-farmed noise (AITA, storytelling subs, etc.), and
aggregating the results into a pain-density signal.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urlencode

import httpx

logger = logging.getLogger(__name__)

REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"
USER_AGENT = "IdeaScroller/1.0 (by /u/ideascroller)"
REQUEST_TIMEOUT_SECONDS = 10.0
MAX_CONCURRENT_REQUESTS = 4
MAX_TOP_THREADS = 8
SELFTEXT_PREVIEW_CHARS = 240
DEFAULT_SEARCH_LIMIT = 100

# Client-side quality thresholds.
MAX_THREAD_SCORE = 1000
MIN_THREAD_COMMENTS = 2

# Quality-score formula constants.
QUALITY_SCORE_CAP = 500
QUALITY_COMMENT_WEIGHT = 3

# Verdict thresholds (applied after client-side filtering).
HOT_THREADS_THRESHOLD = 20
HOT_UPVOTES_THRESHOLD = 500
WARM_THREADS_MIN = 6
WARM_THREADS_MAX = 20
WARM_UPVOTES_MIN = 50
WARM_UPVOTES_MAX = 500

VERDICT_HOT = "hot"
VERDICT_WARM = "warm"
VERDICT_COOL = "cool"
VERDICT_DEAD = "dead"
VERDICT_UNCHECKED = "unchecked"

REMOVED_SELFTEXT_MARKERS = frozenset({"[removed]", "[deleted]"})

# Field-scoped, quoted pain phrases. Each MUST be emitted as
# ``selftext:"<phrase>"`` in the outgoing ``q`` parameter so Reddit treats
# the phrase as an exact match inside the post body rather than as a bag
# of keywords across the whole document.
_PAIN_PHRASES: tuple[str, ...] = (
    '"i wish there was"',
    '"is there an app"',
    '"does anyone know an app"',
    '"why is there no"',
    '"can someone build"',
    '"i\'m looking for an app"',
    '"why doesn\'t"',
    '"tired of"',
)

# Subreddits dominated by storytelling / engagement-farmed content that
# pollute pain-phrase searches. Filtered client-side, case-insensitive.
_BLOCKED_SUBREDDITS: frozenset[str] = frozenset(
    {
        "amitheasshole",
        "aitah",
        "amithedevil",
        "tifu",
        "relationship_advice",
        "relationships",
        "confession",
        "offmychest",
        "trueoffmychest",
        "bestofredditorupdates",
        "nosleep",
        "letsnotmeet",
        "entitledparents",
        "choosingbeggars",
        "maliciouscompliance",
        "prorevenge",
        "pettyrevenge",
        "talesfromretail",
        "talesfromyourservice",
        "askreddit",
        "showerthoughts",
        "unpopularopinion",
        "casualconversation",
        "teenagers",
        "copypasta",
        "hfy",
    }
)


@dataclass(frozen=True)
class RedditThread:
    """A single Reddit thread surfaced by search."""

    title: str
    subreddit: str
    url: str
    score: int
    num_comments: int
    created_utc: float
    selftext_preview: str
    over_18: bool = False
    permalink: str = ""


@dataclass(frozen=True)
class RedditSignal:
    """Aggregated Reddit pain-density signal for a keyword."""

    checked: bool
    error: str | None
    total_threads_found: int
    top_threads: list[RedditThread] = field(default_factory=list)
    total_upvotes: int = 0
    total_comments: int = 0
    subreddits: list[str] = field(default_factory=list)
    verdict: str = VERDICT_UNCHECKED


# ---------------------------------------------------------------------------
# Query construction
# ---------------------------------------------------------------------------


def _strip_phrase_quotes(quoted_phrase: str) -> str:
    """Return the literal phrase text inside a ``"..."`` wrapper.

    ``_PAIN_PHRASES`` entries are authored with surrounding double quotes so
    they can be dropped straight into a ``selftext:"..."`` query. For
    client-side substring checks we need the unquoted form.
    """
    if len(quoted_phrase) >= 2 and quoted_phrase.startswith('"') and quoted_phrase.endswith('"'):
        return quoted_phrase[1:-1]
    return quoted_phrase


def _pain_phrase_literals() -> tuple[str, ...]:
    """Lowercased unquoted pain phrases for client-side substring matching."""
    return tuple(_strip_phrase_quotes(p).lower() for p in _PAIN_PHRASES)


def _build_query(pain_phrase: str, keyword: str) -> str:
    """Build the ``q`` value for a single pain-phrase search.

    Emits ``selftext:"<phrase>" <keyword tokens>``. The ``selftext:``
    binding is what forces Reddit to apply the quoted phrase against the
    post body instead of treating it as loose keywords.
    """
    tokens = keyword.strip()
    if tokens:
        return f"selftext:{pain_phrase} {tokens}"
    return f"selftext:{pain_phrase}"


def _search_params(pain_phrase: str, keyword: str, *, limit: int) -> dict[str, str]:
    return {
        "q": _build_query(pain_phrase, keyword),
        "sort": "new",
        "t": "year",
        "limit": str(limit),
        "restrict_sr": "0",
    }


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _parse_thread(child: dict) -> RedditThread | None:
    """Parse a single Reddit listing child into a RedditThread (or None)."""
    data = child.get("data") if isinstance(child, dict) else None
    if not isinstance(data, dict):
        return None
    permalink = data.get("permalink")
    title = data.get("title")
    subreddit = data.get("subreddit")
    if not permalink or not title or not subreddit:
        return None
    try:
        score = int(data.get("score", 0) or 0)
        num_comments = int(data.get("num_comments", 0) or 0)
        created_utc = float(data.get("created_utc", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    selftext_full = data.get("selftext") or ""
    return RedditThread(
        title=str(title),
        subreddit=str(subreddit),
        url=f"https://reddit.com{permalink}",
        score=score,
        num_comments=num_comments,
        created_utc=created_utc,
        selftext_preview=selftext_full[:SELFTEXT_PREVIEW_CHARS],
        over_18=bool(data.get("over_18", False)),
        permalink=str(permalink),
    )


def _parse_threads(payload: object) -> list[RedditThread]:
    """Parse the Reddit listing JSON envelope into threads."""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, dict):
        return []
    children = data.get("children")
    if not isinstance(children, list):
        return []
    return [t for c in children if (t := _parse_thread(c)) is not None]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


async def _fetch_with_retry(
    client: httpx.AsyncClient, params: dict[str, str]
) -> httpx.Response | None:
    """Fetch with a single retry on 5xx or timeout. None on network failure."""
    headers = {"User-Agent": USER_AGENT}
    # Reddit's ``q`` parameter expects ``+`` for spaces inside its Lucene-like
    # syntax; ``urlencode(..., quote_via=quote_plus)`` produces exactly that.
    encoded_query = urlencode(params, quote_via=quote_plus)
    url = f"{REDDIT_SEARCH_URL}?{encoded_query}"
    for attempt in (1, 2):
        try:
            response = await client.get(url, headers=headers)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning(
                "Reddit net error (attempt %s) for %r: %s",
                attempt,
                params.get("q", ""),
                exc,
            )
            if attempt == 2:
                return None
            continue
        if response.status_code >= 500 and attempt == 1:
            logger.warning(
                "Reddit %s (attempt 1) for %r, retrying",
                response.status_code,
                params.get("q", ""),
            )
            continue
        return response
    return None


def _extract_threads(
    response: httpx.Response | None, query: str
) -> list[RedditThread] | None:
    """Turn a response into threads. Returns None only on network failure."""
    if response is None:
        return None
    if response.status_code >= 400:
        logger.info("Reddit returned %s for %r", response.status_code, query)
        return []
    try:
        return _parse_threads(response.json())
    except ValueError as exc:
        logger.warning("Reddit JSON decode failed for %r: %s", query, exc)
        return []


async def search_reddit(
    query: str,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    client: httpx.AsyncClient | None = None,
) -> list[RedditThread]:
    """Search Reddit for ``query``, returning a list of parsed threads.

    Low-level helper — unlike :func:`check_pain_density`, it does NOT apply
    the client-side quality filter. Returns an empty list on any non-2xx
    or network error; never raises.
    """
    if not isinstance(query, str) or not query.strip():
        logger.warning("search_reddit called with empty query")
        return []
    if limit <= 0:
        return []
    params = {
        "q": query,
        "sort": "new",
        "t": "year",
        "limit": str(limit),
        "restrict_sr": "0",
    }
    owns_client = client is None
    active = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        response = await _fetch_with_retry(active, params)
        return _extract_threads(response, query) or []
    finally:
        if owns_client:
            await active.aclose()


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


async def _run_searches(
    keyword: str,
    phrases: tuple[str, ...],
    *,
    limit: int,
    client: httpx.AsyncClient,
) -> list[list[RedditThread] | None]:
    """Run all pain-phrase searches concurrently with a semaphore cap."""
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def run_one(phrase: str) -> list[RedditThread] | None:
        params = _search_params(phrase, keyword, limit=limit)
        async with semaphore:
            try:
                response = await _fetch_with_retry(client, params)
            except Exception as exc:  # defensive — never leak
                logger.warning(
                    "Unexpected Reddit error for %r: %s", params.get("q"), exc
                )
                return None
            return _extract_threads(response, params.get("q", ""))

    return await asyncio.gather(*(run_one(p) for p in phrases))


def _dedupe_threads(threads: Iterable[RedditThread]) -> list[RedditThread]:
    """Dedupe threads by URL, preserving first-seen order."""
    seen: set[str] = set()
    unique: list[RedditThread] = []
    for t in threads:
        if t.url not in seen:
            seen.add(t.url)
            unique.append(t)
    return unique


def _ordered_subreddits(threads: Iterable[RedditThread]) -> list[str]:
    """Return unique subreddits in first-seen order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for t in threads:
        if t.subreddit not in seen:
            seen.add(t.subreddit)
            ordered.append(t.subreddit)
    return ordered


def _has_pain_phrase(thread: RedditThread, phrase_literals: tuple[str, ...]) -> bool:
    """True if ANY pain phrase appears in title or selftext (case-insensitive).

    We match against ``selftext_preview`` — the same content already
    capped at :data:`SELFTEXT_PREVIEW_CHARS`. Every pain phrase is short
    enough that it will always fit inside the preview window for any
    thread where it occurs early in the body (the common case for
    question-style posts).
    """
    title_lower = thread.title.lower()
    body_lower = thread.selftext_preview.lower()
    return any(lit in title_lower or lit in body_lower for lit in phrase_literals)


def _passes_quality_filter(
    thread: RedditThread, phrase_literals: tuple[str, ...]
) -> bool:
    """Apply every client-side quality gate from the spec."""
    if thread.subreddit.lower() in _BLOCKED_SUBREDDITS:
        return False
    if thread.over_18:
        return False
    if thread.score > MAX_THREAD_SCORE:
        return False
    if thread.num_comments < MIN_THREAD_COMMENTS:
        return False
    body = thread.selftext_preview.strip()
    if not body:
        return False
    if body in REMOVED_SELFTEXT_MARKERS:
        return False
    return _has_pain_phrase(thread, phrase_literals)


def _quality_score(thread: RedditThread) -> int:
    """Rank threads by discussion depth, not raw virality.

    ``min(score, 500)`` caps outlier posts so one viral hit can't
    dominate the list; weighting ``num_comments`` by 3 privileges
    threads with real back-and-forth over drive-by upvotes.
    """
    return min(thread.score, QUALITY_SCORE_CAP) + thread.num_comments * QUALITY_COMMENT_WEIGHT


def _classify_verdict(total_threads: int, total_upvotes: int) -> str:
    """Map aggregated stats to a verdict string."""
    if total_threads == 0:
        return VERDICT_DEAD
    if total_threads > HOT_THREADS_THRESHOLD or total_upvotes >= HOT_UPVOTES_THRESHOLD:
        return VERDICT_HOT
    if (
        WARM_THREADS_MIN <= total_threads <= WARM_THREADS_MAX
        or WARM_UPVOTES_MIN <= total_upvotes <= WARM_UPVOTES_MAX
    ):
        return VERDICT_WARM
    return VERDICT_COOL


def _unchecked(reason: str) -> RedditSignal:
    return RedditSignal(
        checked=False,
        error=reason,
        total_threads_found=0,
        verdict=VERDICT_UNCHECKED,
    )


async def check_pain_density(
    keyword: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> RedditSignal:
    """Run pain-phrased searches against ``keyword`` and aggregate results.

    Strategy: for each phrase in :data:`_PAIN_PHRASES`, issue a
    ``selftext:"..."`` query sorted by ``new`` within the past year, then
    apply a client-side quality filter (blocklist, engagement ceiling,
    phrase verification, etc.) before aggregating. If every search fails
    at the network level, returns ``unchecked`` with ``checked=False``.
    """
    if not isinstance(keyword, str) or not keyword.strip():
        return _unchecked("empty keyword")

    owns_client = client is None
    active = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        results = await _run_searches(
            keyword, _PAIN_PHRASES, limit=DEFAULT_SEARCH_LIMIT, client=active
        )
    finally:
        if owns_client:
            await active.aclose()

    if all(r is None for r in results):
        return _unchecked("all reddit searches failed")

    phrase_literals = _pain_phrase_literals()
    collected = [t for r in results if r for t in r]
    unique = _dedupe_threads(collected)
    filtered = [t for t in unique if _passes_quality_filter(t, phrase_literals)]

    ranked = sorted(filtered, key=_quality_score, reverse=True)
    top = ranked[:MAX_TOP_THREADS]
    total_upvotes = sum(t.score for t in top)

    return RedditSignal(
        checked=True,
        error=None,
        total_threads_found=len(filtered),
        top_threads=top,
        total_upvotes=total_upvotes,
        total_comments=sum(t.num_comments for t in top),
        subreddits=_ordered_subreddits(filtered),
        verdict=_classify_verdict(len(filtered), total_upvotes),
    )
