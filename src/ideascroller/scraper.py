"""Playwright engine for scrolling TikTok FYP and scraping comments."""

import asyncio
import logging
import re
from pathlib import Path
from typing import Callable, Optional

from playwright.async_api import Page, Response, async_playwright

from ideascroller.models import Comment, Video

logger = logging.getLogger(__name__)


def parse_comment_count(text: str) -> int:
    text = text.strip().upper()
    if not text:
        return 0
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    for suffix, mult in multipliers.items():
        if text.endswith(suffix):
            try:
                return int(float(text[:-1]) * mult)
            except ValueError:
                return 0
    try:
        return int(text)
    except ValueError:
        return 0


class Scraper:
    def __init__(self, chrome_user_data_dir: str, comment_threshold: int = 300,
                 on_log: Optional[Callable[[str], None]] = None,
                 on_stats_update: Optional[Callable[[int, int, int], None]] = None) -> None:
        self._chrome_dir = chrome_user_data_dir
        self._threshold = comment_threshold
        self._on_log = on_log or (lambda msg: None)
        self._on_stats_update = on_stats_update or (lambda a, b, c: None)
        self._stop_event = asyncio.Event()
        self._videos: list[Video] = []
        self._comments: list[Comment] = []
        self._videos_scanned = 0
        self._videos_scraped = 0
        self._intercepted_video_items: list[dict] = []
        self._intercepted_comments: list[dict] = []

    @property
    def videos(self) -> list[Video]:
        return list(self._videos)

    @property
    def comments(self) -> list[Comment]:
        return list(self._comments)

    @property
    def videos_scanned(self) -> int:
        return self._videos_scanned

    @property
    def videos_scraped(self) -> int:
        return self._videos_scraped

    def stop(self) -> None:
        self._stop_event.set()

    def _log(self, message: str) -> None:
        logger.info(message)
        self._on_log(message)

    def _update_stats(self) -> None:
        self._on_stats_update(self._videos_scanned, self._videos_scraped, len(self._comments))

    async def _handle_response(self, response: Response) -> None:
        url = response.url
        try:
            if "/api/post/item_list/" in url or "/api/recommend/item_list/" in url:
                body = await response.json()
                items = body.get("itemList", [])
                self._intercepted_video_items.extend(items)
            elif "/api/comment/list/" in url:
                body = await response.json()
                comments_data = body.get("comments", [])
                if comments_data:
                    self._intercepted_comments.extend(comments_data)
        except Exception as e:
            logger.debug("Failed to parse intercepted response: %s", e)

    @staticmethod
    def _get_profile_dir(chrome_user_data_dir: str) -> str:
        """Get a dedicated Playwright profile directory.

        We use a persistent profile at ~/.ideascroller/profile so the user
        only needs to log into TikTok once. Playwright's bundled Chromium
        can't use the real Chrome profile (different encryption keys), so
        this is a separate profile managed by Playwright.
        """
        profile_dir = Path.home() / ".ideascroller" / "profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        return str(profile_dir)

    async def run(self, session_id: str) -> None:
        """Main scroll loop — runs until stop() is called."""
        profile_dir = self._get_profile_dir(self._chrome_dir)
        self._log("Launching browser...")

        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                profile_dir, headless=False,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            page.on("response", self._handle_response)
            self._log("Navigating to TikTok FYP...")
            await page.goto("https://www.tiktok.com/foryou", wait_until="networkidle")
            await asyncio.sleep(3)

            # Check if user needs to log in
            if "login" in page.url.lower():
                self._log("TikTok login required — please log in manually in the browser window")
                # Wait for the user to log in (check URL every 3 seconds)
                while "login" in page.url.lower() and not self._stop_event.is_set():
                    await asyncio.sleep(3)
                if self._stop_event.is_set():
                    await context.close()
                    return
                self._log("Login detected! Navigating to FYP...")
                await page.goto("https://www.tiktok.com/foryou", wait_until="networkidle")
                await asyncio.sleep(3)

            self._log("Starting scroll loop...")
            while not self._stop_event.is_set():
                await self._process_current_video(page, session_id)
                if self._stop_event.is_set():
                    break
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(2)
            self._log("Scroll loop stopped. Closing browser...")
            await context.close()

    async def _process_current_video(self, page: Page, session_id: str) -> None:
        self._videos_scanned += 1
        try:
            count_el = page.locator('strong[data-e2e="comment-count"]').first
            count_text = await count_el.inner_text(timeout=3000)
            comment_count = parse_comment_count(count_text)
        except Exception:
            comment_count = 0

        current_url = page.url
        video_id = self._extract_video_id(current_url)

        if video_id and self._intercepted_video_items:
            for item in self._intercepted_video_items:
                if item.get("id") == video_id:
                    comment_count = item.get("stats", {}).get("commentCount", comment_count)
                    break

        self._log(f"Video #{self._videos_scanned}: {comment_count} comments")
        self._update_stats()

        if comment_count < self._threshold:
            return
        if not video_id:
            self._log("Could not extract video ID, skipping")
            return

        try:
            author_el = page.locator('h3[data-e2e="video-author-uniqueid"]').first
            author = await author_el.inner_text(timeout=3000)
        except Exception:
            author = "unknown"

        try:
            desc_el = page.locator('div[data-e2e="video-desc"]').first
            description = await desc_el.inner_text(timeout=3000)
        except Exception:
            description = ""

        video = Video(id=video_id, session_id=session_id, author=author,
                      description=description[:500], comment_count=comment_count, url=current_url)
        self._videos.append(video)
        self._videos_scraped += 1
        self._log(f"Scraping comments for video by @{author} ({comment_count} comments)...")
        await self._scrape_comments(page, video_id)
        self._update_stats()

    async def _scrape_comments(self, page: Page, video_id: str) -> None:
        self._intercepted_comments.clear()
        try:
            comment_btn = page.locator('div[data-e2e="comment-button"]').first
            await comment_btn.click(timeout=5000)
            await asyncio.sleep(2)
        except Exception as e:
            self._log(f"Failed to open comment panel: {e}")
            return

        prev_count = 0
        stall_count = 0
        max_stalls = 3
        while stall_count < max_stalls:
            current_count = len(self._intercepted_comments)
            if current_count > prev_count:
                prev_count = current_count
                stall_count = 0
            else:
                stall_count += 1
            try:
                await page.evaluate("""() => {
                    const panel = document.querySelector('div[class*="DivCommentListContainer"]')
                        || document.querySelector('div[class*="DivCommentMain"]');
                    if (panel) panel.scrollTop = panel.scrollHeight;
                }""")
            except Exception:
                pass
            await asyncio.sleep(1.5)

        for raw_comment in self._intercepted_comments:
            try:
                comment = Comment(
                    id=raw_comment.get("cid", ""), video_id=video_id,
                    text=raw_comment.get("text", ""),
                    author=raw_comment.get("user", {}).get("unique_id", "unknown"),
                    likes=raw_comment.get("digg_count", 0),
                    reply_count=raw_comment.get("reply_comment_total", 0))
                self._comments.append(comment)
            except Exception as e:
                logger.debug("Failed to parse comment: %s", e)

        self._log(f"Collected {len(self._intercepted_comments)} comments from video {video_id}")
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)
        except Exception:
            pass

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        match = re.search(r"/video/(\d+)", url)
        return match.group(1) if match else None
