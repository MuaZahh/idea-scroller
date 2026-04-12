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
    def __init__(
        self,
        chrome_user_data_dir: str,
        comment_threshold: int = 300,
        on_log: Optional[Callable[[str], None]] = None,
        on_stats_update: Optional[Callable[[int, int, int], None]] = None,
    ) -> None:
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
        self._on_stats_update(
            self._videos_scanned, self._videos_scraped, len(self._comments)
        )

    async def _handle_response(self, response: Response) -> None:
        url = response.url
        try:
            if (
                "/api/post/item_list/" in url
                or "/api/recommend/item_list/" in url
                or "/api/preload/item_list/" in url
            ):
                body = await response.json()
                items = body.get("itemList", [])
                if items:
                    self._intercepted_video_items.extend(items)
                    self._log(
                        f"Intercepted {len(items)} video items (total: {len(self._intercepted_video_items)})"
                    )
            elif "/api/comment/list/" in url:
                body = await response.json()
                comments_data = body.get("comments", [])
                if comments_data:
                    self._intercepted_comments.extend(comments_data)
        except Exception as e:
            logger.debug("Failed to parse response %s: %s", url[:80], e)

    @staticmethod
    def _get_profile_dir(chrome_user_data_dir: str) -> str:
        profile_dir = Path.home() / ".ideascroller" / "profile"
        profile_dir.mkdir(parents=True, exist_ok=True)
        return str(profile_dir)

    # ------------------------------------------------------------------
    # DOM helpers — all scoped to the VISIBLE article element
    # ------------------------------------------------------------------

    async def _read_visible_video(self, page: Page) -> dict:
        """Read all info from the currently visible article in ONE atomic call.

        Returns dict with: scrollIndex, author, commentText, desc, articleId
        Everything comes from the same DOM element so it's always consistent.
        """
        try:
            info = await page.evaluate("""() => {
                const articles = document.querySelectorAll(
                    'article[data-e2e="recommend-list-item-container"]'
                );
                for (const art of articles) {
                    const rect = art.getBoundingClientRect();
                    if (rect.top >= -200 && rect.top < window.innerHeight / 2) {
                        let author = null;
                        const links = art.querySelectorAll('a[href*="/@"]');
                        for (const link of links) {
                            const m = link.href.match(/\\/@([^/?]+)/);
                            if (m) { author = m[1]; break; }
                        }
                        const countEl = art.querySelector('strong[data-e2e="comment-count"]');
                        const descEl = art.querySelector('div[data-e2e="video-desc"]');
                        return {
                            scrollIndex: parseInt(art.getAttribute('data-scroll-index') || '-1'),
                            articleId: art.id || '',
                            author: author,
                            commentText: countEl ? countEl.textContent : '0',
                            desc: descEl ? descEl.textContent.substring(0, 500) : '',
                        };
                    }
                }
                return null;
            }""")
            return info or {}
        except Exception:
            return {}

    async def _click_comment_button_in_article(self, page: Page, article_id: str) -> bool:
        """Click the comment button INSIDE the specific article element.

        This prevents clicking the wrong video's comment button.
        """
        try:
            clicked = await page.evaluate("""(articleId) => {
                let art = null;
                if (articleId) {
                    art = document.getElementById(articleId);
                }
                if (!art) {
                    // Fallback: find visible article
                    const articles = document.querySelectorAll(
                        'article[data-e2e="recommend-list-item-container"]'
                    );
                    for (const a of articles) {
                        const rect = a.getBoundingClientRect();
                        if (rect.top >= -200 && rect.top < window.innerHeight / 2) {
                            art = a;
                            break;
                        }
                    }
                }
                if (!art) return false;
                const icon = art.querySelector('span[data-e2e="comment-icon"]');
                if (!icon) return false;
                const btn = icon.closest('button') || icon.parentElement;
                if (!btn) return false;
                btn.click();
                return true;
            }""", article_id)
            return clicked
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Video ID resolution
    # ------------------------------------------------------------------

    def _find_video_id(self, author: Optional[str], desc: str) -> Optional[str]:
        """Find video ID from intercepted XHR data by matching author + description."""
        if not author:
            return None

        # Collect all items from this author
        matches = [
            item
            for item in self._intercepted_video_items
            if item.get("author", {}).get("uniqueId") == author
        ]

        if not matches:
            return None

        if len(matches) == 1:
            return matches[0].get("id")

        # Multiple videos by same author — disambiguate by description overlap
        desc_words = set(desc.lower().split()) if desc else set()
        if desc_words:
            best_item = max(
                matches,
                key=lambda item: len(
                    desc_words & set((item.get("desc") or "").lower().split())
                ),
            )
            return best_item.get("id")

        # Can't disambiguate — return most recent
        return matches[-1].get("id")

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        match = re.search(r"/video/(\d+)", url)
        return match.group(1) if match else None

    # ------------------------------------------------------------------
    # Main run loop — strictly sequential
    # ------------------------------------------------------------------

    async def run(self, session_id: str) -> None:
        profile_dir = self._get_profile_dir(self._chrome_dir)
        self._log("Launching browser...")

        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                profile_dir,
                headless=False,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            page.on("response", self._handle_response)

            self._log("Navigating to TikTok FYP...")
            await page.goto("https://www.tiktok.com/foryou", wait_until="networkidle")
            await asyncio.sleep(3)

            # Handle login if needed
            if "login" in page.url.lower():
                self._log("TikTok login required — log in manually in the browser")
                while "login" in page.url.lower() and not self._stop_event.is_set():
                    await asyncio.sleep(3)
                if self._stop_event.is_set():
                    await context.close()
                    return
                self._log("Login detected! Navigating to FYP...")
                await page.goto(
                    "https://www.tiktok.com/foryou", wait_until="networkidle"
                )
                await asyncio.sleep(3)

            self._log("Starting scroll loop...")
            last_article_id = ""

            while not self._stop_event.is_set():
                # 1. READ the video that is currently on screen
                video_info = await self._read_visible_video(page)
                article_id = video_info.get("articleId", "")
                author = video_info.get("author")
                comment_text = video_info.get("commentText", "0")
                description = video_info.get("desc", "")
                comment_count = parse_comment_count(comment_text)

                # Skip if we already processed this exact article
                if article_id and article_id == last_article_id:
                    # Same article — just scroll
                    await page.keyboard.press("ArrowDown")
                    await asyncio.sleep(1.5)
                    continue

                last_article_id = article_id
                self._videos_scanned += 1

                # 2. RESOLVE video ID
                video_id = self._find_video_id(author, description)
                if not video_id:
                    video_id = self._extract_video_id(page.url)

                id_str = video_id or "no ID"
                self._log(
                    f"Video #{self._videos_scanned} @{author}: "
                    f"{comment_count} comments ({id_str})"
                )
                self._update_stats()

                # 3. CHECK threshold — if below, scroll to next
                if comment_count < self._threshold:
                    await page.keyboard.press("ArrowDown")
                    await asyncio.sleep(1.5)
                    continue

                # 4. SCRAPE this video — do NOT scroll until done
                if not video_id:
                    self._log(f"No video ID for @{author}, skipping")
                    await page.keyboard.press("ArrowDown")
                    await asyncio.sleep(1.5)
                    continue

                if any(v.id == video_id for v in self._videos):
                    self._log(f"Already scraped {video_id}, skipping")
                    await page.keyboard.press("ArrowDown")
                    await asyncio.sleep(1.5)
                    continue

                video = Video(
                    id=video_id,
                    session_id=session_id,
                    author=author or "unknown",
                    description=description[:500],
                    comment_count=comment_count,
                    url=page.url,
                )
                self._videos.append(video)
                self._videos_scraped += 1

                self._log(
                    f">>> Scraping @{author} ({comment_count} comments)..."
                )
                await self._scrape_comments(page, video_id, article_id)
                self._update_stats()

                # 5. DONE scraping — NOW scroll to next video
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(1.5)

            self._log("Scroll loop stopped. Closing browser...")
            await context.close()

    # ------------------------------------------------------------------
    # Comment scraping — scoped to the correct article
    # ------------------------------------------------------------------

    async def _scrape_comments(
        self, page: Page, video_id: str, article_id: str
    ) -> None:
        self._intercepted_comments.clear()

        # Click the comment button INSIDE this specific article
        opened = await self._click_comment_button_in_article(page, article_id)
        if not opened:
            self._log("Failed to open comment panel")
            return

        await asyncio.sleep(2)

        # Scroll the comment panel to load more comments via XHR
        prev_count = 0
        stall_count = 0
        max_stalls = 3

        while stall_count < max_stalls:
            current_count = len(self._intercepted_comments)
            if current_count > prev_count:
                self._log(f"  ...{current_count} comments loaded")
                prev_count = current_count
                stall_count = 0
            else:
                stall_count += 1

            try:
                await page.evaluate("""() => {
                    const selectors = [
                        'div[class*="DivCommentListContainer"]',
                        'div[class*="DivCommentMain"]',
                        'div[class*="CommentList"]',
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el && el.scrollHeight > el.clientHeight) {
                            el.scrollTop = el.scrollHeight;
                            return;
                        }
                    }
                }""")
            except Exception:
                pass

            await asyncio.sleep(1)

        # Store collected comments
        for raw in self._intercepted_comments:
            try:
                self._comments.append(
                    Comment(
                        id=raw.get("cid", ""),
                        video_id=video_id,
                        text=raw.get("text", ""),
                        author=raw.get("user", {}).get("unique_id", "unknown"),
                        likes=raw.get("digg_count", 0),
                        reply_count=raw.get("reply_comment_total", 0),
                    )
                )
            except Exception as e:
                logger.debug("Failed to parse comment: %s", e)

        self._log(f"Collected {len(self._intercepted_comments)} comments")

        # Close comment panel
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)
        except Exception:
            pass
