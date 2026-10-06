"""Fan-out orchestrator that attaches market-validation signals to each cluster.

Runs TrustMRR + Reddit + Google-Trends + session virality concurrently per cluster.
Every validator is already individually bulletproof (never raises) — this layer
just schedules them and serializes results into ``ValidationSignals``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, is_dataclass
from typing import Callable, Iterable

from ideascroller.models import (
    AnalysisCluster,
    ValidationSignals,
    Video,
)
from ideascroller.validators.keywords import check_keyword_async
from ideascroller.validators.reddit import check_pain_density
from ideascroller.validators.trustmrr import validate_idea

logger = logging.getLogger(__name__)


_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ai", ("ai", "llm", "gpt", "chatbot", "generative", "machine learning", "ml")),
    (
        "fintech",
        ("invoice", "tax", "bank", "payment", "bookkeep", "accounting", "finance", "budget"),
    ),
    ("ecommerce", ("shopify", "ecommerce", "dropship", "storefront", "merchant")),
    ("health", ("fitness", "calorie", "workout", "health", "wellness", "mental", "sleep", "diet")),
    ("education", ("learn", "study", "flashcard", "tutor", "course", "school", "exam")),
    ("productivity", ("todo", "task", "schedule", "calendar", "note", "writing", "journal")),
    ("social", ("social", "community", "dating", "friends", "group chat", "network")),
    ("marketing", ("seo", "ads", "marketing", "newsletter", "email list", "content")),
)


def infer_trustmrr_category(theme: str, app_idea: str) -> str:
    """Map a cluster's free-text description to a TrustMRR category slug.

    Uses word-boundary matching so short keywords like ``"ai"`` don't false-match
    inside larger words (e.g. ``"daily"``). Falls through to ``"saas"`` when
    nothing obvious matches.
    """
    blob = f"{theme} {app_idea}".lower()
    for category, keywords in _CATEGORY_KEYWORDS:
        for kw in keywords:
            pattern = re.escape(kw)
            if re.search(rf"\b{pattern}\b", blob):
                return category
    return "saas"


_STOPWORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "app", "for", "to", "of", "that", "this", "with", "and",
        "or", "is", "are", "be", "ai", "on", "in", "at", "by", "from",
    }
)


def derive_search_keyword(theme: str, app_idea: str) -> str:
    """Build a short 2–4 word keyword string for Reddit + Trends queries."""
    source = theme if theme else app_idea
    tokens = re.findall(r"[a-zA-Z][a-zA-Z\-']+", source.lower())
    meaningful = [t for t in tokens if t not in _STOPWORDS and len(t) > 2]
    return " ".join(meaningful[:3]) if meaningful else source.strip()[:40]


def _to_dict(obj: object) -> dict:
    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump()  # type: ignore[no-any-return]
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    return {}


def compute_session_virality(videos: Iterable[Video]) -> dict:
    """Aggregate view/like counters across a scraping session."""
    videos_list = list(videos)
    if not videos_list:
        return {"videos": 0, "total_views": 0, "avg_views": 0, "max_views": 0, "total_likes": 0}
    views = [v.view_count for v in videos_list]
    likes = [v.like_count for v in videos_list]
    total_views = sum(views)
    return {
        "videos": len(videos_list),
        "total_views": total_views,
        "avg_views": total_views // len(videos_list),
        "max_views": max(views),
        "total_likes": sum(likes),
    }


async def _enrich_single_cluster(
    cluster: AnalysisCluster,
    *,
    trustmrr_api_key: str | None,
    session_virality: dict,
    log: Callable[[str], None],
) -> AnalysisCluster:
    category = infer_trustmrr_category(cluster.theme, cluster.app_idea)
    keyword = derive_search_keyword(cluster.theme, cluster.app_idea)
    log(f"  Validating '{cluster.theme[:40]}' → category={category} keyword='{keyword}'")

    trustmrr_task = validate_idea(
        trustmrr_api_key,
        theme=cluster.theme,
        app_idea=cluster.app_idea,
        category_hint=category,
    )
    reddit_task = check_pain_density(keyword)
    keyword_task = check_keyword_async(keyword)

    trustmrr_res, reddit_res, keyword_res = await asyncio.gather(
        trustmrr_task, reddit_task, keyword_task, return_exceptions=True,
    )

    def _safe(name: str, result: object) -> dict | None:
        if isinstance(result, Exception):
            logger.warning("Validator %s raised for cluster '%s': %s", name, cluster.theme[:40], result)
            return {"checked": False, "error": f"{type(result).__name__}: {result}"}
        return _to_dict(result)

    signals = ValidationSignals(
        trustmrr=_safe("trustmrr", trustmrr_res),
        reddit=_safe("reddit", reddit_res),
        keyword=_safe("keyword", keyword_res),
        virality=session_virality,
    )
    return cluster.model_copy(update={"validation": signals})


async def enrich_clusters(
    clusters: list[AnalysisCluster],
    videos: list[Video],
    *,
    trustmrr_api_key: str | None = None,
    enabled: bool = True,
    log: Callable[[str], None] | None = None,
) -> list[AnalysisCluster]:
    """Attach ``ValidationSignals`` to each cluster in parallel.

    Immutable: returns a fresh list of clusters (uses ``model_copy``).
    """
    _log = log or (lambda msg: logger.info(msg))
    if not clusters:
        return clusters
    if not enabled:
        _log("Validators disabled — returning clusters unchanged")
        return clusters

    virality = compute_session_virality(videos)
    _log(f"Running validators on {len(clusters)} cluster(s) (virality: {virality})")

    tasks = [
        _enrich_single_cluster(
            c,
            trustmrr_api_key=trustmrr_api_key,
            session_virality=virality,
            log=_log,
        )
        for c in clusters
    ]
    return list(await asyncio.gather(*tasks))
