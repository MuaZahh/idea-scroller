"""Quick smoke: one cluster, real Sonnet 4.6, real tools. See what Claude does."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from ideascroller.adaptive_analyzer import validate_cluster  # noqa: E402
from ideascroller.models import AnalysisCluster  # noqa: E402


CLUSTER = AnalysisCluster(
    theme="AI photo-based calorie counter",
    summary="Users tired of manual entry / barcode scanning in MFP want to snap a photo of their plate.",
    comment_count=6,
    video_count=2,
    potential="HIGH",
    app_idea="Mobile app that uses vision AI to identify food + portion size from a photo and auto-log macros",
    competitors=["MyFitnessPal", "Cal AI", "Lose It!"],
    market="GROWING",
    edge="MFP is bloated and barcode-based; Cal AI is close but expensive",
    sample_comments=[
        "I wish there was an app where I could just take a photo of my plate",
        "MFP barcode scanner is broken half the time and the database is wrong",
        "all calorie apps want $80/yr and still make you enter everything manually",
        "Cal AI is close but it's expensive and inaccurate on homemade meals",
        "please someone make a simple calorie counter that doesn't have ads",
    ],
)


async def main() -> int:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("ANTHROPIC_API_KEY missing", flush=True)
        return 1
    print(f"=== Running adaptive validator on 1 cluster (real Sonnet 4.6) ===\n", flush=True)
    enriched, trace = await validate_cluster(key, CLUSTER, log=print)
    print("\n" + "=" * 70)
    print("FINAL VERDICT (from Claude):")
    print(json.dumps(trace.final_verdict, indent=2))
    print("\n" + "=" * 70)
    print(f"PLAN (search_plan was called {trace.plan is not None}):")
    if trace.plan:
        print(json.dumps(trace.plan, indent=2))
    print("\n" + "=" * 70)
    print(f"TOOL CALL SEQUENCE ({len(trace.tool_calls)} calls across {trace.turns} turn(s)):")
    for i, call in enumerate(trace.tool_calls, 1):
        inp = call["input"]
        if call["tool"] == "search_plan":
            summary = "planned"
        elif call["tool"] == "think":
            summary = (inp.get("thought", "") or "")[:80]
        else:
            qk = next((k for k in ("query", "keyword", "seed_phrase", "topic") if k in inp), None)
            summary = f"{qk}={inp.get(qk)!r}" if qk else ""
        angle = f" angle={inp.get('angle')}" if "angle" in inp else ""
        print(f"  [{i}] {call['tool']:<28} {summary[:70]}{angle} → {call['result_summary']}")
    if trace.errors:
        print("\nERRORS:", trace.errors)
    return 0 if trace.final_verdict else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
