"""Live end-to-end walkthrough of the IdeaScroller pipeline.

Phases 1 and 2 are faked (we don't want to actually scroll TikTok or burn an LLM
key for a demo). Phase 3 makes REAL calls to TrustMRR, Reddit, and Google Trends
so you can see what a real idea card looks like after validation.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from ideascroller.models import AnalysisCluster, Comment, Video
from ideascroller.validators.orchestrator import (
    compute_session_virality,
    derive_search_keyword,
    enrich_clusters,
    infer_trustmrr_category,
)

load_dotenv(Path(__file__).parent.parent / ".env")


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(f"  {title}")
    print("=" * 78)


def dump(obj: object, indent: int = 2) -> str:
    return json.dumps(obj, indent=indent, default=str)


# ============================================================================
# PHASE 1 (faked) — what the scraper would produce after ~20 minutes of scrolling
# ============================================================================


def fake_phase_1() -> tuple[list[Video], list[Comment]]:
    videos = [
        Video(
            id="7123000001", session_id="demo", author="fitgirl_x",
            description="day 5 of counting calories with my new app",
            comment_count=2_400, url="https://tiktok.com/1",
            view_count=4_200_000, like_count=380_000, share_count=18_000, save_count=52_000,
        ),
        Video(
            id="7123000002", session_id="demo", author="mealprep_mom",
            description="the REAL problem with MyFitnessPal no one talks about",
            comment_count=1_800, url="https://tiktok.com/2",
            view_count=1_900_000, like_count=140_000, share_count=22_000, save_count=31_000,
        ),
        Video(
            id="7123000003", session_id="demo", author="nails_by_jen",
            description="when your dog's nails get too long and you miss the grooming appt again",
            comment_count=920, url="https://tiktok.com/3",
            view_count=680_000, like_count=42_000, share_count=3_100, save_count=8_400,
        ),
    ]

    # Realistic example comments per video
    comments = [
        # Video 1 — calorie tracking frustrations
        Comment(id="c1", video_id="7123000001", text="I hate how MFP makes me scan every barcode manually", author="u1", likes=420),
        Comment(id="c2", video_id="7123000001", text="I wish there was an app where I could just take a photo of my plate", author="u2", likes=1_800),
        Comment(id="c3", video_id="7123000001", text="why is counting calories so annoying, please someone make a simple one", author="u3", likes=940),
        Comment(id="c4", video_id="7123000001", text="all calorie apps are either too complex or want $80/yr", author="u4", likes=560),
        # Video 2 — MFP specifically
        Comment(id="c5", video_id="7123000002", text="I literally pay for MFP premium and it still sucks at recipes", author="u5", likes=310),
        Comment(id="c6", video_id="7123000002", text="the AI one I saw on here last week was so much better but can't remember the name", author="u6", likes=220),
        # Video 3 — pet nails
        Comment(id="c7", video_id="7123000003", text="I wish there was an app that reminded me when to trim my dog's nails", author="u7", likes=150),
        Comment(id="c8", video_id="7123000003", text="is there an app that tracks pet grooming schedules?", author="u8", likes=88),
        Comment(id="c9", video_id="7123000003", text="literally googled this last week and nothing good exists", author="u9", likes=64),
    ]
    return videos, comments


# ============================================================================
# PHASE 2 (faked) — what the LLM would produce after running analyze_comments
# ============================================================================


def fake_phase_2(videos: list[Video], comments: list[Comment]) -> list[AnalysisCluster]:
    return [
        AnalysisCluster(
            theme="AI photo-based calorie counter",
            summary=(
                "Users are fed up with manual barcode scanning in MyFitnessPal and want "
                "to point a camera at their plate. High frustration across multiple "
                "comments — this is a repeat, mass complaint, not a one-off."
            ),
            comment_count=6,
            video_count=2,
            potential="HIGH",
            app_idea="Mobile app that uses vision AI to identify food + portion size from a photo and auto-log macros",
            competitors=["MyFitnessPal", "Cal AI", "Lose It!"],
            market="GROWING",
            edge="MFP is bloated and barcode-based; Cal AI is close but expensive",
            sample_comments=[
                "I wish there was an app where I could just take a photo of my plate",
                "all calorie apps are either too complex or want $80/yr",
                "why is counting calories so annoying",
            ],
        ),
        AnalysisCluster(
            theme="Pet grooming schedule reminders",
            summary=(
                "Multiple commenters said they forget to trim their dog's nails and "
                "couldn't find a dedicated app. Small but concrete painpoint."
            ),
            comment_count=3,
            video_count=1,
            potential="HIGH",
            app_idea="App that tracks pet grooming cadence (nails, bath, ears) and sends reminders based on breed/size",
            competitors=[],
            market="OPEN",
            edge="No dedicated pet-grooming reminder app found",
            sample_comments=[
                "I wish there was an app that reminded me when to trim my dog's nails",
                "is there an app that tracks pet grooming schedules?",
                "literally googled this last week and nothing good exists",
            ],
        ),
    ]


# ============================================================================
# PHASE 3 — run the REAL validators
# ============================================================================


async def run_phase_3(clusters: list[AnalysisCluster], videos: list[Video]) -> list[AnalysisCluster]:
    trustmrr_key = os.environ.get("TRUSTMRR_API_KEY") or None
    print(f"TRUSTMRR_API_KEY: {'present' if trustmrr_key else 'MISSING (will skip)'}")
    start = time.time()
    enriched = await enrich_clusters(
        clusters, videos, trustmrr_api_key=trustmrr_key, enabled=True,
        log=lambda msg: print(f"  [orchestrator] {msg}"),
    )
    print(f"\n  elapsed: {time.time() - start:.2f}s")
    return enriched


def print_card(i: int, c: AnalysisCluster) -> None:
    v = c.validation
    print(f"\n--- IDEA CARD {i + 1} " + "-" * 60)
    print(f"  theme:      {c.theme}")
    print(f"  app idea:   {c.app_idea}")
    print(f"  LLM market: {c.market}   edge: {c.edge}")
    print(f"  inferred category: {infer_trustmrr_category(c.theme, c.app_idea)}")
    print(f"  derived keyword:   '{derive_search_keyword(c.theme, c.app_idea)}'")
    if not v:
        print("  (no validation attached)"); return

    # TrustMRR
    tm = v.trustmrr or {}
    print(f"\n  📊 TrustMRR  verdict={tm.get('verdict'):<10} "
          f"max_mrr=${tm.get('max_mrr_usd', 0):,.0f}  "
          f">=100K/mo: {tm.get('competitors_above_100k_mrr', 0)}  "
          f">=10K/mo: {tm.get('competitors_above_10k_mrr', 0)}")
    for comp in (tm.get("top_competitors") or [])[:3]:
        print(f"      • {comp['name']:<25} ${comp['mrr_usd']:>10,.0f}/mo   {comp.get('website') or ''}")

    # Reddit
    rd = v.reddit or {}
    print(f"\n  💬 Reddit    verdict={rd.get('verdict'):<10} "
          f"threads={rd.get('total_threads_found', 0)}  "
          f"upvotes={rd.get('total_upvotes', 0)}  "
          f"subs={', '.join((rd.get('subreddits') or [])[:4])}")
    for th in (rd.get("top_threads") or [])[:3]:
        print(f"      • [r/{th['subreddit']}] ({th['score']}↑ {th['num_comments']}💬) {th['title'][:80]}")

    # Keyword signal (Google Autocomplete + Wikipedia)
    kw = v.keyword or {}
    if kw.get("checked"):
        src = kw.get("source", "?")
        sugg_count = kw.get("suggestion_count", 0)
        wiki_views = kw.get("wikipedia_monthly_views")
        wiki_fmt = f"{wiki_views:,}/mo" if wiki_views is not None else "no article"
        exact = "✓" if kw.get("exact_phrase_is_suggestion") else "✗"
        print(f"\n  📈 Keyword   verdict={kw.get('verdict'):<10} "
              f"source={src:<22} "
              f"suggestions={sugg_count} ({exact} exact match)  "
              f"wiki={wiki_fmt}")
        if kw.get("autocomplete_suggestions"):
            top = kw["autocomplete_suggestions"][:5]
            print(f"      suggestions: {', '.join(top)}")
    else:
        print(f"\n  📈 Keyword   unchecked ({kw.get('error', 'no data')})")

    # Virality
    vi = v.virality or {}
    print(f"\n  🎬 Virality  videos={vi.get('videos', 0)}  "
          f"total_views={vi.get('total_views', 0):,}  "
          f"max={vi.get('max_views', 0):,}  "
          f"avg={vi.get('avg_views', 0):,}")


async def main() -> None:
    section("PHASE 1 — what the scraper would hand us (faked)")
    videos, comments = fake_phase_1()
    for v in videos:
        print(f"  📹 @{v.author}  {v.view_count:>10,} views  "
              f"{v.like_count:>8,} ♥  {v.comment_count:>6,} 💬  '{v.description[:60]}'")
    print(f"\n  → {len(videos)} videos, {len(comments)} comments collected")

    section("PHASE 2 — what the LLM would produce (faked)")
    clusters = fake_phase_2(videos, comments)
    for c in clusters:
        print(f"\n  💡 {c.theme}")
        print(f"     app_idea: {c.app_idea[:90]}")
        print(f"     market: {c.market}   competitors: {c.competitors or 'none named'}")
        print(f"     sample: \"{c.sample_comments[0][:80]}\"")
    print(f"\n  → {len(clusters)} clusters")

    section("PHASE 3 — LIVE validator run (real HTTP calls)")
    enriched = await run_phase_3(clusters, videos)

    section("RESULT — final idea cards")
    for i, c in enumerate(enriched):
        print_card(i, c)
    print()


if __name__ == "__main__":
    asyncio.run(main())
