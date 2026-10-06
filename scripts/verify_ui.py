"""Playwright UI smoke test — drive the running dev server, inject a synthetic
validation-rich cluster via ``renderClusters``, assert all 4 badges appear,
take a screenshot of the rendered idea card.

Must be run while ``ideascroller`` is serving on 127.0.0.1:8000.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

SERVER_URL = "http://127.0.0.1:8000"
SCREENSHOT_PATH = Path(__file__).parent.parent / "ui_verify_screenshot.png"

SYNTHETIC_CLUSTER = {
    "theme": "AI photo-based calorie counter",
    "summary": "Users want to snap a photo of their plate and have AI log it automatically.",
    "comment_count": 42,
    "video_count": 3,
    "potential": "HIGH",
    "app_idea": "Mobile app using vision AI to identify food + macros from a photo",
    "competitors": ["MyFitnessPal", "Cal AI", "Lose It!"],
    "market": "GROWING",
    "edge": "MFP is bloated and barcode-based; Cal AI is close but expensive",
    "sample_comments": [
        "I wish there was an app where I could just take a photo of my plate",
        "all calorie apps are either too complex or want $80/yr",
        "why is counting calories so annoying",
    ],
    "validation": {
        "trustmrr": {
            "checked": True, "error": None, "verdict": "strong",
            "verdict_source": "fuzzy_match",
            "top_competitors": [
                {"name": "MacroFactor", "slug": "macrofactor",
                 "description": "AI calorie tracker", "website": "https://macrofactorapp.com",
                 "category": "health-fitness", "mrr_usd": 180_000.0,
                 "total_revenue_usd": 4_500_000.0, "growth_mrr_30d": 0.15,
                 "customers": 60_000, "url": "https://trustmrr.com/startups/macrofactor"},
                {"name": "CalBuddy", "slug": "calbuddy",
                 "description": "Calorie counting app", "website": "https://calbuddy.app",
                 "category": "mobile-apps", "mrr_usd": 45_000.0,
                 "total_revenue_usd": 900_000.0, "growth_mrr_30d": 0.22,
                 "customers": 12_000, "url": "https://trustmrr.com/startups/calbuddy"},
            ],
            "max_mrr_usd": 180_000.0,
            "competitors_above_10k_mrr": 2,
            "competitors_above_100k_mrr": 1,
            "category": "health-fitness",
        },
        "reddit": {
            "checked": True, "error": None, "verdict": "hot",
            "total_threads_found": 18,
            "total_upvotes": 1024, "total_comments": 312,
            "subreddits": ["loseit", "MacroFactor", "1200isplenty", "fitness"],
            "top_threads": [
                {"title": "Wish there was a photo-based calorie tracker",
                 "subreddit": "loseit", "url": "https://reddit.com/r/loseit/x",
                 "score": 412, "num_comments": 88, "created_utc": 1_710_000_000.0,
                 "selftext_preview": "I'm so tired of manual entry..."},
                {"title": "Is there an app that just looks at my plate?",
                 "subreddit": "MacroFactor", "url": "https://reddit.com/r/MacroFactor/y",
                 "score": 280, "num_comments": 65, "created_utc": 1_710_000_000.0,
                 "selftext_preview": "I've tried all the big ones..."},
            ],
        },
        "keyword": {
            "checked": True, "error": None,
            "keyword": "photo calorie counter",
            "verdict": "growing",
            "autocomplete_suggestions": [
                "photo calorie counter", "photo calorie counter app", "photo calorie tracker",
                "ai calorie counter", "photo calorie scanner",
            ],
            "suggestion_count": 8, "exact_phrase_is_suggestion": True,
            "wikipedia_monthly_views": 45_000, "wikipedia_article_title": "Calorie",
            "search_volume": None, "cpc_usd": None,
            "source": "autocomplete+wikipedia",
        },
        "virality": {
            "videos": 3, "total_views": 6_780_000, "avg_views": 2_260_000,
            "max_views": 4_200_000, "total_likes": 562_000,
        },
    },
}


async def main() -> int:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await context.new_page()

        console_errors: list[str] = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: console_errors.append(f"PAGEERROR: {err}"))

        await page.goto(SERVER_URL, wait_until="networkidle")
        print(f"[1/5] Loaded {SERVER_URL}")

        # Make the results section visible (it's hidden until analysis completes)
        await page.evaluate("""
            const res = document.getElementById('results');
            if (res) res.style.display = 'block';
        """)

        # Inject the synthetic cluster directly through renderClusters
        await page.evaluate("""(cluster) => {
            window.lastClusters = [cluster];
            renderClusters([cluster]);
        }""", SYNTHETIC_CLUSTER)
        print("[2/5] Injected synthetic cluster via renderClusters()")

        # Assert badges exist
        badges = await page.locator(".validation-badges .vbadge").all()
        print(f"[3/5] Found {len(badges)} validation badges")
        if len(badges) < 4:
            print(f"  FAIL — expected ≥4 (TrustMRR, Reddit, Keyword, Virality), got {len(badges)}")
            await browser.close()
            return 1

        # Extract + print badge text content
        texts = []
        for b in badges:
            t = (await b.text_content() or "").strip()
            cls = await b.get_attribute("class") or ""
            tip = await b.get_attribute("data-tooltip") or ""
            texts.append((cls, t, tip[:80]))
        print("[4/5] Badge contents:")
        for cls, t, tip in texts:
            print(f"    class={cls!r}  text={t!r}  tooltip(80)={tip!r}")

        # Verify expected classes present
        all_classes = " ".join(cls for cls, _, _ in texts)
        expected_markers = ["vb-tm-strong", "vb-rd-hot", "vb-kw-growing", "vb-vir-high"]
        missing = [m for m in expected_markers if m not in all_classes]
        if missing:
            print(f"  FAIL — missing badge classes: {missing}")
            await browser.close()
            return 1

        # Screenshot
        await page.locator(".cluster").first.screenshot(path=str(SCREENSHOT_PATH))
        print(f"[5/5] Screenshot saved to {SCREENSHOT_PATH}")

        if console_errors:
            print("\nCONSOLE ERRORS:")
            for err in console_errors:
                print(f"  - {err}")

        await browser.close()
        print("\n✅ UI verification PASSED")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
