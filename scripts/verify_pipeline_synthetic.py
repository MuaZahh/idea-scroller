"""Deterministic end-to-end proof: synthetic pain-point comments + real videos
with realistic view counts → REAL Anthropic LLM analysis → REAL validators.

Proves the complete pipeline handles virality correctly post-schema-fix.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from ideascroller.analyzer import analyze_comments  # noqa: E402
from ideascroller.models import Comment, Video  # noqa: E402

SID = "verify-synthetic"

VIDEOS = [
    Video(id="v1", session_id=SID, author="fitgirl_x",
          description="day 5 of counting calories", comment_count=30,
          url="https://tiktok.com/1",
          view_count=4_315_426, like_count=380_000, share_count=18_000, save_count=52_000),
    Video(id="v2", session_id=SID, author="mealprep_mom",
          description="the REAL problem with MyFitnessPal", comment_count=25,
          url="https://tiktok.com/2",
          view_count=1_920_000, like_count=140_000, share_count=22_000, save_count=31_000),
    Video(id="v3", session_id=SID, author="nails_by_jen",
          description="when you forget to trim your dog's nails", comment_count=15,
          url="https://tiktok.com/3",
          view_count=680_000, like_count=42_000, share_count=3_100, save_count=8_400),
]

COMMENTS = [
    # calorie-counter pain
    Comment(id=f"c{i}", video_id="v1", text=t, author=f"u{i}", likes=l)
    for i, (t, l) in enumerate([
        ("I wish there was an app where I could just snap a photo of my plate and it would log everything.", 1800),
        ("MyFitnessPal's barcode scanning is broken half the time", 940),
        ("all calorie apps want $80/yr and still make you enter everything manually", 560),
        ("please someone make a simple calorie counter that doesn't have ads", 420),
        ("Cal AI is close but it's expensive and inaccurate on homemade meals", 310),
    ])
] + [
    Comment(id=f"d{i}", video_id="v2", text=t, author=f"u{i}", likes=l)
    for i, (t, l) in enumerate([
        ("I literally pay for MFP premium and it still sucks at recipes", 380),
        ("why doesn't anyone just photo-scan a meal? all apps make you type", 220),
        ("I want something as easy as taking a picture", 180),
    ])
] + [
    Comment(id=f"e{i}", video_id="v3", text=t, author=f"u{i}", likes=l)
    for i, (t, l) in enumerate([
        ("I wish there was an app that reminded me when to trim my dog's nails", 150),
        ("is there an app that tracks pet grooming schedules per breed?", 88),
        ("literally googled this last week and nothing good exists", 64),
    ])
]


async def main() -> int:
    api_keys = {k: os.environ[k] for k in ("ANTHROPIC_API_KEY", "TRUSTMRR_API_KEY") if os.environ.get(k)}
    print(f"[1/3] {len(VIDEOS)} videos · {len(COMMENTS)} comments · keys={list(api_keys)}")
    print(f"      total_views={sum(v.view_count for v in VIDEOS):,}  max={max(v.view_count for v in VIDEOS):,}")

    print("[2/3] Running real LLM + real validators...")
    result = await analyze_comments(
        api_keys=api_keys, session_id=SID, videos=VIDEOS, comments=COMMENTS,
        on_log=lambda msg: print(f"    {msg}"),
        mode="balanced", enable_validators=True,
    )
    print(f"\n[3/3] Got {len(result.clusters)} cluster(s)\n")

    ok = True
    for i, c in enumerate(result.clusters, 1):
        v = c.validation
        print(f"#{i}  {c.theme}")
        print(f"    idea:    {c.app_idea[:100]}")
        print(f"    market:  {c.market}  edge: {c.edge[:80]}")
        print(f"    comments={c.comment_count}  videos={c.video_count}")
        if not v:
            print("    ⚠ no validation attached"); ok = False; continue
        tm = v.trustmrr or {}
        rd = v.reddit or {}
        kw = v.keyword or {}
        vi = v.virality or {}
        comps = tm.get("top_competitors") or []
        top_line = ", ".join(f"{x['name']} ${x.get('mrr_usd',0):,.0f}" for x in comps[:3])
        print(f"    📊 TrustMRR  {tm.get('verdict'):<10}  max=${tm.get('max_mrr_usd',0):,.0f}/mo  [{top_line}]")
        print(f"    💬 Reddit    {rd.get('verdict'):<10}  {rd.get('total_threads_found')} threads · {rd.get('total_upvotes')} upvotes · subs={rd.get('subreddits', [])[:4]}")
        print(f"    📈 Keyword   {kw.get('verdict'):<10}  suggestions={kw.get('suggestion_count')}  wiki={kw.get('wikipedia_monthly_views')}  src={kw.get('source')}")
        print(f"    🔥 Virality  videos={vi.get('videos')}  total={vi.get('total_views'):,}  max={vi.get('max_views'):,}  avg={vi.get('avg_views'):,}")
        print()
        if vi.get("max_views", 0) == 0:
            print(f"    ⚠ FAIL: virality max_views=0"); ok = False

    if not result.clusters:
        print("FAIL — no clusters surfaced"); return 1
    print("✅ PIPELINE VERIFIED END-TO-END" if ok else "❌ PIPELINE HAS BUGS")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
