"""TrustMRR validator: competitor discovery via fuzzy match against a cached corpus.

Paginates the ~840-startup TrustMRR dataset (cached 24h) and uses rapidfuzz
WRatio scoring to surface real competitors for an idea. ``validate_idea`` is the
primary entry point; ``validate_category`` is kept as a legacy fallback. No
public async function raises — errors become an ``unchecked`` ``TrustMRRSignal``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from rapidfuzz import fuzz, process

logger = logging.getLogger(__name__)

TRUSTMRR_BASE_URL = "https://trustmrr.com/api/v1"
TRUSTMRR_STARTUP_URL_TEMPLATE = "https://trustmrr.com/startups/{slug}"
REQUEST_TIMEOUT_SECONDS = 10.0

# TrustMRR rate-limits 20 req/min per key; cap concurrent in-flight requests to 20.
_RATE_LIMIT_PER_MINUTE = 20
_rate_limit_semaphore = asyncio.Semaphore(_RATE_LIMIT_PER_MINUTE)

PAGINATION_LIMIT = 50  # API max page size.
MAX_PAGES = 100  # Safety cap — dataset is ~840 rows (~17 pages).

STRONG_MRR_THRESHOLD_USD = 100_000.0
VIABLE_MRR_THRESHOLD_USD = 10_000.0

VERDICT_STRONG = "strong"
VERDICT_VIABLE = "viable"
VERDICT_WEAK = "weak"
VERDICT_ABSENT = "absent"
VERDICT_UNCHECKED = "unchecked"

VERDICT_SOURCE_FUZZY = "fuzzy_match"
VERDICT_SOURCE_CATEGORY_FALLBACK = "category_fallback"
VERDICT_SOURCE_UNCHECKED = "unchecked"

DEFAULT_TOP_N = 5

_CACHE_DIR = Path.home() / ".ideascroller" / "cache"
DEFAULT_CACHE_TTL_HOURS = 24

DEFAULT_MIN_FUZZY_SCORE = 75  # Raised from 60 — below this, matches tend to be noise
                              # (e.g. "Stan" matching "AI calorie counter" on just "AI" overlap)
DEFAULT_FUZZY_TOP_K = 20
CATEGORY_BOOST = 10.0

@dataclass(frozen=True)
class TrustMRRCompetitor:
    """A single startup listed on TrustMRR, normalized to USD dollars."""
    name: str
    slug: str
    description: str | None
    website: str | None
    category: str | None
    mrr_usd: float
    total_revenue_usd: float
    growth_mrr_30d: float | None
    customers: int | None
    url: str

@dataclass(frozen=True)
class TrustMRRSignal:
    """Aggregate competitor signal for one category or idea."""
    category: str
    checked: bool
    error: str | None
    top_competitors: list[TrustMRRCompetitor]
    max_mrr_usd: float
    competitors_above_10k_mrr: int
    competitors_above_100k_mrr: int
    verdict: str
    verdict_source: str = VERDICT_SOURCE_UNCHECKED
    fuzzy_scores: tuple[float, ...] = field(default_factory=tuple)

@dataclass(frozen=True)
class FuzzyMatch:
    """One fuzzy-match hit between an idea query and a TrustMRR startup."""
    competitor: TrustMRRCompetitor
    score: float  # 0-100 from rapidfuzz; may exceed 100 after category boost.
    matched_on: str  # "name" | "description" | "category"

def _cents_to_dollars(value: Any) -> float:
    """Convert a cents integer to USD dollars; missing/invalid → 0.0."""
    if value is None:
        return 0.0
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return 0.0

def _coerce_optional(value: Any, caster: type) -> Any:
    """Coerce ``value`` with ``caster`` when possible, else None."""
    if value is None:
        return None
    try:
        return caster(value)
    except (TypeError, ValueError):
        return None

def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None

def _parse_competitor(raw: dict[str, Any]) -> TrustMRRCompetitor | None:
    """Parse one raw startup dict; returns None if required fields are missing."""
    name, slug = raw.get("name"), raw.get("slug")
    if not isinstance(name, str) or not isinstance(slug, str):
        logger.warning("Skipping TrustMRR record missing name/slug: %r", raw)
        return None
    revenue = raw.get("revenue") if isinstance(raw.get("revenue"), dict) else {}
    return TrustMRRCompetitor(
        name=name, slug=slug,
        description=_optional_str(raw.get("description")),
        website=_optional_str(raw.get("website")),
        category=_optional_str(raw.get("category")),
        mrr_usd=_cents_to_dollars(revenue.get("mrr")),
        total_revenue_usd=_cents_to_dollars(revenue.get("total")),
        growth_mrr_30d=_coerce_optional(raw.get("growthMRR30d"), float),
        customers=_coerce_optional(raw.get("customers"), int),
        url=TRUSTMRR_STARTUP_URL_TEMPLATE.format(slug=slug),
    )

def _sorted_by_mrr_desc(competitors: list[TrustMRRCompetitor]) -> list[TrustMRRCompetitor]:
    """Return a new list sorted by MRR desc (does not mutate input)."""
    return sorted(competitors, key=lambda c: c.mrr_usd, reverse=True)

async def _get_startups(
    client: httpx.AsyncClient, api_key: str, params: dict[str, Any],
) -> dict[str, Any]:
    """GET /startups with the given params; returns the parsed JSON envelope."""
    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{TRUSTMRR_BASE_URL}/startups"
    async with _rate_limit_semaphore:
        response = await client.get(url, params=params, headers=headers)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected TrustMRR response shape: {type(payload).__name__}")
    data = payload.get("data", [])
    if not isinstance(data, list):
        raise ValueError("TrustMRR 'data' field is not a list")
    return payload

async def _request_startups(
    client: httpx.AsyncClient, api_key: str, category: str,
    *, limit: int, min_revenue_cents: int | None,
) -> list[dict[str, Any]]:
    """GET /startups filtered by category; returns the raw ``data`` list."""
    params: dict[str, Any] = {"category": category, "sort": "revenue-desc", "limit": limit}
    if min_revenue_cents is not None:
        params["minRevenue"] = min_revenue_cents
    payload = await _get_startups(client, api_key, params)
    return payload["data"]

async def _request_page(
    client: httpx.AsyncClient, api_key: str, *, page: int, limit: int,
) -> tuple[list[dict[str, Any]], bool]:
    """Fetch one page of the full startup list. Returns (records, has_more)."""
    params: dict[str, Any] = {"page": page, "limit": limit, "sort": "revenue-desc"}
    payload = await _get_startups(client, api_key, params)
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    return payload["data"], bool(meta.get("hasMore", False))

async def fetch_top_startups(
    api_key: str, category: str,
    *,
    limit: int = DEFAULT_TOP_N,
    min_revenue_cents: int | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[TrustMRRCompetitor]:
    """Fetch top startups in ``category`` sorted by MRR desc. Raises on HTTP errors."""
    if not api_key:
        raise ValueError("api_key is required")
    if not category:
        raise ValueError("category is required")
    if limit <= 0:
        raise ValueError("limit must be positive")
    owns_client = client is None
    active = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        raw_records = await _request_startups(
            active, api_key, category, limit=limit, min_revenue_cents=min_revenue_cents,
        )
    finally:
        if owns_client:
            await active.aclose()
    parsed = [_parse_competitor(r) for r in raw_records if isinstance(r, dict)]
    return _sorted_by_mrr_desc([c for c in parsed if c is not None])

def _cache_dir() -> Path:
    """Indirection so tests can monkeypatch ``_CACHE_DIR``."""
    return _CACHE_DIR

def _corpus_path() -> Path:
    return _cache_dir() / "trustmrr_corpus.jsonl"

def _synced_at_path() -> Path:
    return _cache_dir() / "trustmrr_corpus.synced_at"

def _write_corpus(records: list[dict[str, Any]]) -> None:
    """Atomically write corpus JSONL + synced_at timestamp."""
    _cache_dir().mkdir(parents=True, exist_ok=True)
    corpus_path = _corpus_path()
    tmp_path = corpus_path.with_suffix(corpus_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False))
            fh.write("\n")
    os.replace(tmp_path, corpus_path)
    _synced_at_path().write_text(datetime.now(tz=timezone.utc).isoformat(), encoding="utf-8")

def load_cached_corpus() -> list[dict[str, Any]]:
    """Return parsed JSONL from cache, or [] if missing. Does NOT check TTL."""
    path = _corpus_path()
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line_num, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    logger.warning("Corrupt TrustMRR corpus line %d: %s", line_num, exc)
                    continue
                if isinstance(obj, dict):
                    records.append(obj)
    except OSError as exc:
        logger.warning("Failed to read TrustMRR corpus cache: %s", exc)
        return []
    return records

def is_cache_fresh(ttl_hours: int = DEFAULT_CACHE_TTL_HOURS) -> bool:
    """Return True iff the synced_at file is newer than ``ttl_hours`` ago."""
    path = _synced_at_path()
    if not path.exists():
        return False
    try:
        synced_at = datetime.fromisoformat(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        logger.warning("Invalid synced_at file, treating cache as stale: %s", exc)
        return False
    if synced_at.tzinfo is None:
        synced_at = synced_at.replace(tzinfo=timezone.utc)
    return datetime.now(tz=timezone.utc) - synced_at < timedelta(hours=ttl_hours)

async def sync_corpus(
    api_key: str,
    *,
    force: bool = False,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Paginate the full startup list and write it to the local cache.

    When ``force=False`` and the cache is fresh, returns cached records without
    hitting the network. Raises on transport/response errors.
    """
    if not api_key:
        raise ValueError("api_key is required")

    if not force and is_cache_fresh():
        cached = load_cached_corpus()
        if cached:
            logger.info("TrustMRR cache fresh (%d records) — skipping sync", len(cached))
            return cached

    owns_client = client is None
    active = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
    all_records: list[dict[str, Any]] = []
    page = 1
    sync_error: Exception | None = None
    try:
        while page <= MAX_PAGES:
            try:
                records, has_more = await _request_page(
                    active, api_key, page=page, limit=PAGINATION_LIMIT,
                )
            except Exception as exc:  # noqa: BLE001 — we persist partial progress below
                sync_error = exc
                break
            all_records.extend(r for r in records if isinstance(r, dict))
            if not has_more:
                break
            page += 1
            # TrustMRR rate limit is 20 req/min — space requests ~3s apart.
            await asyncio.sleep(3.2)
    finally:
        if owns_client:
            await active.aclose()

    # Persist whatever we got — even on partial failure we'd rather have 500
    # records cached than re-start from scratch next run.
    if all_records:
        _write_corpus(all_records)
        logger.info(
            "Synced TrustMRR corpus: %d records across %d page(s)%s",
            len(all_records), page, " (partial)" if sync_error else "",
        )
    if sync_error and not all_records:
        raise sync_error
    if sync_error:
        logger.warning("TrustMRR sync stopped at page %d: %s", page, sync_error)
    return all_records

def _searchable_text(raw: dict[str, Any]) -> str:
    """Build the scorer's target string: name + ' ' + description."""
    name = raw.get("name") if isinstance(raw.get("name"), str) else ""
    description = raw.get("description") if isinstance(raw.get("description"), str) else ""
    return f"{name} {description}".strip()

def _matched_field(query: str, raw: dict[str, Any]) -> str:
    """Identify which field drove the best match (for explainability)."""
    name = raw.get("name") if isinstance(raw.get("name"), str) else ""
    description = raw.get("description") if isinstance(raw.get("description"), str) else ""
    category = raw.get("category") if isinstance(raw.get("category"), str) else ""
    scores = (
        ("name", fuzz.WRatio(query, name) if name else 0.0),
        ("description", fuzz.WRatio(query, description) if description else 0.0),
        ("category", fuzz.WRatio(query, category) if category else 0.0),
    )
    return max(scores, key=lambda pair: pair[1])[0]

def match_competitors(
    query: str,
    corpus: list[dict[str, Any]],
    *,
    min_score: int = DEFAULT_MIN_FUZZY_SCORE,
    top_k: int = DEFAULT_FUZZY_TOP_K,
    category_boost: tuple[str, ...] = (),
) -> list[FuzzyMatch]:
    """Fuzzy-match ``query`` against startup records using rapidfuzz WRatio.

    Scores each candidate against ``name + ' ' + description``. Adds a flat
    ``CATEGORY_BOOST`` (10.0) when the startup's category is in ``category_boost``.
    Returns up to ``top_k`` matches with score >= ``min_score``, sorted desc.
    """
    if not query or not query.strip() or not corpus:
        return []

    valid = [r for r in corpus if isinstance(r, dict) and isinstance(r.get("slug"), str)]
    if not valid:
        return []

    choices = [_searchable_text(r) for r in valid]

    # Over-select so boosts can reorder before we cap at top_k.
    raw_hits = process.extract(
        query, choices, scorer=fuzz.WRatio,
        limit=max(top_k * 3, top_k), score_cutoff=0,
    )

    boost_set = {slug.lower() for slug in category_boost if slug}
    query_words = _meaningful_words(query)
    scored: list[FuzzyMatch] = []
    for _, base_score, idx in raw_hits:
        record = valid[idx]
        competitor = _parse_competitor(record)
        if competitor is None:
            continue
        score = float(base_score)
        cat = (competitor.category or "").lower()
        if cat and cat in boost_set:
            score += CATEGORY_BOOST
        if score < min_score:
            continue
        # Semantic-overlap gate: at least one meaningful query word must actually
        # appear in the competitor's searchable text. Without this, short company
        # names like "Stan" match any long query via partial_ratio.
        target_words = _meaningful_words(_searchable_text(record))
        if query_words and not (query_words & target_words):
            continue
        scored.append(FuzzyMatch(
            competitor=competitor, score=score, matched_on=_matched_field(query, record),
        ))
    scored.sort(key=lambda m: m.score, reverse=True)
    return scored[:top_k]


_FUZZY_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "at", "by",
    "with", "is", "are", "be", "app", "apps", "tool", "tools", "platform",
    "software", "system", "service", "solution", "based", "ai", "ml",
})


def _meaningful_words(text: str) -> frozenset[str]:
    """Lowercased word set with short words + stopwords stripped."""
    tokens = re.findall(r"[a-zA-Z][a-zA-Z\-']+", text.lower())
    return frozenset(t for t in tokens if len(t) >= 4 and t not in _FUZZY_STOPWORDS)

def _compute_verdict(competitors: list[TrustMRRCompetitor]) -> str:
    """Classify a category by the strongest competitor found."""
    if not competitors:
        return VERDICT_ABSENT
    max_mrr = max(c.mrr_usd for c in competitors)
    if max_mrr >= STRONG_MRR_THRESHOLD_USD:
        return VERDICT_STRONG
    if max_mrr >= VIABLE_MRR_THRESHOLD_USD:
        return VERDICT_VIABLE
    if max_mrr > 0:
        return VERDICT_WEAK
    return VERDICT_ABSENT

def _build_signal(
    category: str,
    competitors: list[TrustMRRCompetitor],
    *,
    verdict_source: str = VERDICT_SOURCE_UNCHECKED,
    fuzzy_scores: tuple[float, ...] = (),
) -> TrustMRRSignal:
    """Assemble a TrustMRRSignal from a list of competitors."""
    sorted_c = _sorted_by_mrr_desc(competitors)
    max_mrr = sorted_c[0].mrr_usd if sorted_c else 0.0
    above_10k = sum(1 for c in sorted_c if c.mrr_usd >= VIABLE_MRR_THRESHOLD_USD)
    above_100k = sum(1 for c in sorted_c if c.mrr_usd >= STRONG_MRR_THRESHOLD_USD)
    return TrustMRRSignal(
        category=category,
        checked=True,
        error=None,
        top_competitors=sorted_c,
        max_mrr_usd=max_mrr,
        competitors_above_10k_mrr=above_10k,
        competitors_above_100k_mrr=above_100k,
        verdict=_compute_verdict(sorted_c),
        verdict_source=verdict_source,
        fuzzy_scores=fuzzy_scores,
    )

def _unchecked_signal(category: str, error: str | None) -> TrustMRRSignal:
    """Build a signal for the case where no check could be performed."""
    return TrustMRRSignal(
        category=category, checked=False, error=error,
        top_competitors=[], max_mrr_usd=0.0,
        competitors_above_10k_mrr=0, competitors_above_100k_mrr=0,
        verdict=VERDICT_UNCHECKED, verdict_source=VERDICT_SOURCE_UNCHECKED,
        fuzzy_scores=(),
    )

def _classify_error(exc: BaseException) -> str:
    """Normalize any caught exception into a short ``error`` string."""
    if isinstance(exc, httpx.TimeoutException):
        return f"timeout: {exc}"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"http {exc.response.status_code}"
    if isinstance(exc, httpx.HTTPError):
        return f"transport: {exc}"
    if isinstance(exc, ValueError):
        return f"invalid payload: {exc}"
    return f"unexpected: {exc}"

async def validate_category(
    api_key: str | None,
    category: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> TrustMRRSignal:
    """Validate a category by fetching its top MRR competitors. Never raises."""
    if not api_key:
        logger.info("TrustMRR validator skipped for %r: no API key configured", category)
        return _unchecked_signal(category, error=None)
    try:
        competitors = await fetch_top_startups(api_key, category, client=client)
    except Exception as exc:  # noqa: BLE001 - defensive: never raise to caller
        logger.warning("TrustMRR error for category %r: %s", category, exc)
        return _unchecked_signal(category, error=_classify_error(exc))
    return _build_signal(category, competitors)

async def _ensure_fresh_corpus(
    api_key: str, *, client: httpx.AsyncClient | None,
) -> list[dict[str, Any]]:
    """Return a fresh corpus; sync from the API when the cache is stale."""
    if is_cache_fresh():
        cached = load_cached_corpus()
        if cached:
            return cached
    return await sync_corpus(api_key, client=client)

async def validate_idea(
    api_key: str | None,
    *,
    theme: str,
    app_idea: str,
    category_hint: str = "saas",
    client: httpx.AsyncClient | None = None,
) -> TrustMRRSignal:
    """Fuzzy-match the theme+idea against the cached corpus for real competitors.

    Falls back to ``validate_category(api_key, category_hint)`` when no fuzzy
    hits clear the threshold. Never raises: all errors become an ``unchecked``
    signal with ``error`` set.
    """
    if not api_key:
        logger.info("TrustMRR validate_idea skipped: no API key configured")
        return _unchecked_signal(category_hint, error=None)

    query = f"{theme or ''} {app_idea or ''}".strip()
    if not query:
        logger.info("TrustMRR validate_idea skipped: empty theme+app_idea")
        return _unchecked_signal(category_hint, error="empty query")

    try:
        corpus = await _ensure_fresh_corpus(api_key, client=client)
    except Exception as exc:  # noqa: BLE001 - defensive: never raise to caller
        logger.warning("TrustMRR corpus sync failed: %s", exc)
        return _unchecked_signal(category_hint, error=_classify_error(exc))

    boost = tuple(dict.fromkeys(s for s in (category_hint, "mobile-apps") if s))
    matches = match_competitors(query, corpus, category_boost=boost)
    if matches:
        top = matches[:DEFAULT_TOP_N]
        return _build_signal(
            category=category_hint,
            competitors=[m.competitor for m in top],
            verdict_source=VERDICT_SOURCE_FUZZY,
            fuzzy_scores=tuple(m.score for m in top),
        )
    logger.info("TrustMRR: no fuzzy matches for %r — falling back to %r", query, category_hint)
    fallback = await validate_category(api_key, category_hint, client=client)
    return replace(fallback, verdict_source=VERDICT_SOURCE_CATEGORY_FALLBACK)
