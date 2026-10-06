"""End-to-end test: scraper → analyzer → validators, all external calls mocked.

Proves the full pipeline (a) captures TikTok video metrics, (b) runs LLM analysis,
(c) fans out TrustMRR + Reddit + Google-Trends + virality validators per cluster,
(d) attaches their signals to ``AnalysisCluster.validation`` without ever raising
if a validator fails.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ideascroller.analyzer import analyze_comments
from ideascroller.models import Comment, Video
from ideascroller.validators.keywords import KeywordSignal
from ideascroller.validators.orchestrator import (
    compute_session_virality,
    derive_search_keyword,
    enrich_clusters,
    infer_trustmrr_category,
)
from ideascroller.validators.reddit import RedditSignal, RedditThread
from ideascroller.validators.trustmrr import TrustMRRCompetitor, TrustMRRSignal


# ---------------------------------------------------------------------------
# Pure helpers — no mocking needed
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_infer_trustmrr_category_ai() -> None:
    assert infer_trustmrr_category("AI writing assistant", "LLM tool") == "ai"


@pytest.mark.unit
def test_infer_trustmrr_category_fintech() -> None:
    assert infer_trustmrr_category("Freelancer invoice tracker", "sends invoices") == "fintech"


@pytest.mark.unit
def test_infer_trustmrr_category_fallback_saas() -> None:
    assert infer_trustmrr_category("Boring enterprise widget", "boring") == "saas"
    assert infer_trustmrr_category("Task scheduler", "daily todo") == "productivity"


@pytest.mark.unit
def test_derive_search_keyword_strips_stopwords() -> None:
    assert derive_search_keyword("An app for tracking plant watering", "plant watering app") == (
        "tracking plant watering"
    )


@pytest.mark.unit
def test_derive_search_keyword_uses_app_idea_if_theme_empty() -> None:
    kw = derive_search_keyword("", "Budgeting tool for freelancers")
    assert "budgeting" in kw


@pytest.mark.unit
def test_compute_session_virality_aggregates_counts() -> None:
    videos = [
        Video(id="1", session_id="s", author="a", description="", comment_count=10,
              url="u", view_count=1000, like_count=50),
        Video(id="2", session_id="s", author="b", description="", comment_count=20,
              url="u", view_count=500000, like_count=80000),
    ]
    out = compute_session_virality(videos)
    assert out["videos"] == 2
    assert out["total_views"] == 501000
    assert out["max_views"] == 500000
    assert out["avg_views"] == 250500
    assert out["total_likes"] == 80050


@pytest.mark.unit
def test_compute_session_virality_empty_videos() -> None:
    out = compute_session_virality([])
    assert out == {"videos": 0, "total_views": 0, "avg_views": 0, "max_views": 0, "total_likes": 0}


# ---------------------------------------------------------------------------
# enrich_clusters — all three external validators mocked at module level
# ---------------------------------------------------------------------------


def _make_cluster_dict(theme: str = "Plant watering tracker") -> dict[str, Any]:
    return {
        "theme": theme,
        "summary": "People want an app to remind them when to water plants",
        "comment_count": 40,
        "video_count": 3,
        "potential": "HIGH",
        "app_idea": "plant watering reminder app",
        "competitors": [],
        "market": "OPEN",
        "edge": "nothing simple exists",
        "sample_comments": ["I always forget to water my plants"],
    }


def _fake_trustmrr_signal() -> TrustMRRSignal:
    comp = TrustMRRCompetitor(
        name="PlantPal",
        slug="plantpal",
        description="Plant care app",
        website="https://plantpal.app",
        category="saas",
        mrr_usd=150_000.0,
        total_revenue_usd=5_000_000.0,
        growth_mrr_30d=0.12,
        customers=50_000,
        url="https://trustmrr.com/startups/plantpal",
    )
    return TrustMRRSignal(
        category="saas",
        checked=True,
        error=None,
        top_competitors=[comp],
        max_mrr_usd=150_000.0,
        competitors_above_10k_mrr=1,
        competitors_above_100k_mrr=1,
        verdict="strong",
        verdict_source="fuzzy_match",
    )


def _fake_reddit_signal() -> RedditSignal:
    thread = RedditThread(
        title="Wish there was an app to track plant watering",
        subreddit="houseplants",
        url="https://reddit.com/r/houseplants/comments/abc",
        score=412,
        num_comments=88,
        created_utc=1_710_000_000.0,
        selftext_preview="I keep killing my plants",
    )
    return RedditSignal(
        checked=True,
        error=None,
        total_threads_found=14,
        top_threads=[thread],
        total_upvotes=850,
        total_comments=203,
        subreddits=["houseplants", "indoorgarden"],
        verdict="hot",
    )


def _fake_keyword_signal() -> KeywordSignal:
    return KeywordSignal(
        checked=True,
        error=None,
        keyword="plant watering tracker",
        autocomplete_suggestions=("plant watering tracker app", "plant watering reminder"),
        suggestion_count=2,
        exact_phrase_is_suggestion=True,
        wikipedia_monthly_views=45_000,
        wikipedia_article_title="Houseplant_care",
        search_volume=None,
        cpc_usd=None,
        source="autocomplete+wikipedia",
        verdict="growing",
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_enrich_clusters_attaches_all_signals() -> None:
    cluster = _make_cluster_dict()
    from ideascroller.models import AnalysisCluster

    clusters = [AnalysisCluster(**cluster)]
    videos = [
        Video(id="1", session_id="s", author="a", description="", comment_count=100,
              url="u", view_count=2_000_000, like_count=150_000, share_count=8_000),
    ]

    with (
        patch(
            "ideascroller.validators.orchestrator.validate_idea",
            new=AsyncMock(return_value=_fake_trustmrr_signal()),
        ),
        patch(
            "ideascroller.validators.orchestrator.check_pain_density",
            new=AsyncMock(return_value=_fake_reddit_signal()),
        ),
        patch(
            "ideascroller.validators.orchestrator.check_keyword_async",
            new=AsyncMock(return_value=_fake_keyword_signal()),
        ),
    ):
        enriched = await enrich_clusters(
            clusters, videos, trustmrr_api_key="tmrr_test", enabled=True,
        )

    assert len(enriched) == 1
    v = enriched[0].validation
    assert v is not None
    assert v.trustmrr["verdict"] == "strong"
    assert v.trustmrr["max_mrr_usd"] == 150_000.0
    assert v.reddit["verdict"] == "hot"
    assert v.reddit["total_threads_found"] == 14
    assert v.keyword["verdict"] == "growing"
    assert v.keyword["wikipedia_monthly_views"] == 45_000
    assert v.keyword["source"] == "autocomplete+wikipedia"
    assert v.virality["total_views"] == 2_000_000
    assert v.virality["max_views"] == 2_000_000
    # Source cluster was not mutated — verify immutability contract
    assert clusters[0].validation is None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_enrich_clusters_tolerates_validator_exceptions() -> None:
    """A raising validator must not sink the pipeline — the cluster still returns
    with a checked=False/error-populated signal for that slot."""
    from ideascroller.models import AnalysisCluster

    clusters = [AnalysisCluster(**_make_cluster_dict())]

    with (
        patch(
            "ideascroller.validators.orchestrator.validate_idea",
            new=AsyncMock(side_effect=RuntimeError("simulated trustmrr crash")),
        ),
        patch(
            "ideascroller.validators.orchestrator.check_pain_density",
            new=AsyncMock(return_value=_fake_reddit_signal()),
        ),
        patch(
            "ideascroller.validators.orchestrator.check_keyword_async",
            new=AsyncMock(return_value=_fake_keyword_signal()),
        ),
    ):
        enriched = await enrich_clusters(clusters, [], trustmrr_api_key="x", enabled=True)

    v = enriched[0].validation
    assert v.trustmrr == {"checked": False, "error": "RuntimeError: simulated trustmrr crash"}
    assert v.reddit["verdict"] == "hot"  # others unaffected
    assert v.keyword["verdict"] == "growing"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_enrich_clusters_disabled_returns_unchanged() -> None:
    from ideascroller.models import AnalysisCluster

    clusters = [AnalysisCluster(**_make_cluster_dict())]
    enriched = await enrich_clusters(clusters, [], enabled=False)
    assert enriched[0].validation is None


# ---------------------------------------------------------------------------
# Full pipeline: analyze_comments end-to-end with mocked LLM + mocked validators
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_analyze_comments_full_pipeline_with_validators() -> None:
    videos = [
        Video(id="v1", session_id="s", author="a", description="plants", comment_count=40,
              url="u", view_count=1_500_000, like_count=80_000, share_count=5_000),
    ]
    comments = [
        Comment(id=f"c{i}", video_id="v1", text="I wish there was an app to remind me",
                author="u", likes=10)
        for i in range(5)
    ]

    fake_llm_json = json.dumps({
        "clusters": [{
            "theme": "Plant watering reminders",
            "summary": "People forget to water their plants",
            "comment_count": 5,
            "video_count": 1,
            "potential": "HIGH",
            "app_idea": "simple plant watering reminder app",
            "competitors": [],
            "market": "OPEN",
            "edge": "nothing lightweight exists",
            "sample_comments": ["I always forget to water my plants"],
        }]
    })

    with (
        patch(
            "ideascroller.analyzer._call_anthropic",
            new=AsyncMock(return_value=fake_llm_json),
        ),
        patch(
            "ideascroller.validators.orchestrator.validate_idea",
            new=AsyncMock(return_value=_fake_trustmrr_signal()),
        ),
        patch(
            "ideascroller.validators.orchestrator.check_pain_density",
            new=AsyncMock(return_value=_fake_reddit_signal()),
        ),
        patch(
            "ideascroller.validators.orchestrator.check_keyword_async",
            new=AsyncMock(return_value=_fake_keyword_signal()),
        ),
    ):
        result = await analyze_comments(
            api_keys={"ANTHROPIC_API_KEY": "sk-test", "TRUSTMRR_API_KEY": "tmrr_test"},
            session_id="s",
            videos=videos,
            comments=comments,
            mode="balanced",
            enable_validators=True,
            validator_mode="rigid",
        )

    assert len(result.clusters) == 1
    cluster = result.clusters[0]
    assert cluster.theme == "Plant watering reminders"
    assert cluster.validation is not None
    assert cluster.validation.trustmrr["verdict"] == "strong"
    assert cluster.validation.reddit["verdict"] == "hot"
    assert cluster.validation.keyword["verdict"] == "growing"
    assert cluster.validation.virality["max_views"] == 1_500_000


@pytest.mark.asyncio
@pytest.mark.integration
async def test_analyze_comments_with_validators_disabled() -> None:
    videos = [
        Video(id="v1", session_id="s", author="a", description="x", comment_count=1, url="u"),
    ]
    comments = [Comment(id="c1", video_id="v1", text="hi", author="u")]

    fake_llm_json = json.dumps({"clusters": [{
        "theme": "T", "summary": "S", "comment_count": 1, "video_count": 1,
        "potential": "HIGH", "app_idea": "A", "competitors": [], "market": "OPEN",
        "edge": "E", "sample_comments": ["c"],
    }]})

    with patch(
        "ideascroller.analyzer._call_anthropic", new=AsyncMock(return_value=fake_llm_json),
    ):
        result = await analyze_comments(
            api_keys={"ANTHROPIC_API_KEY": "sk-test"},
            session_id="s",
            videos=videos,
            comments=comments,
            enable_validators=False,
        )

    assert result.clusters[0].validation is None
