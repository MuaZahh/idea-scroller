"""Playwright engine for scrolling TikTok FYP and scraping comments."""

import asyncio
import logging
import re
import subprocess
import time
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

    async def _extract_initial_items(self, page: Page) -> None:
        """Extract video items from TikTok's server-side rendered data.

        The first batch of FYP videos is embedded in the HTML (not via XHR),
        so the response interceptor misses them. This parses them from
        script tags or the window's state.
        """
        try:
            items = await page.evaluate("""() => {
                // Try various TikTok state locations
                const locations = [
                    // SIGI_STATE (older TikTok)
                    () => {
                        if (window.SIGI_STATE?.ItemModule) {
                            return Object.values(window.SIGI_STATE.ItemModule);
                        }
                    },
                    // __UNIVERSAL_DATA_FOR_REHYDRATION__ (current TikTok)
                    () => {
                        const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
                        if (!el) return null;
                        const data = JSON.parse(el.textContent);
                        const scope = data['__DEFAULT_SCOPE__'] || {};
                        // Search all keys for itemList arrays
                        for (const val of Object.values(scope)) {
                            if (val && val.itemList && Array.isArray(val.itemList)) {
                                return val.itemList;
                            }
                        }
                    },
                    // React fiber state on feed containers
                    () => {
                        const items = [];
                        const articles = document.querySelectorAll(
                            'article[data-e2e="recommend-list-item-container"]'
                        );
                        for (const art of articles) {
                            const fiberKey = Object.keys(art).find(
                                k => k.startsWith('__reactFiber') || k.startsWith('__reactInternalInstance')
                            );
                            if (fiberKey) {
                                let fiber = art[fiberKey];
                                // Walk up to find the item data
                                for (let i = 0; i < 15 && fiber; i++) {
                                    const props = fiber.memoizedProps || fiber.pendingProps;
                                    if (props?.item?.id || props?.data?.id) {
                                        items.push(props.item || props.data);
                                        break;
                                    }
                                    fiber = fiber.return;
                                }
                            }
                        }
                        return items.length > 0 ? items : null;
                    },
                ];
                for (const loc of locations) {
                    try {
                        const result = loc();
                        if (result && result.length > 0) return result;
                    } catch(e) {}
                }
                return [];
            }""")

            if items:
                self._intercepted_video_items.extend(items)
                self._log(f"Extracted {len(items)} initial video items from page")
            else:
                self._log("No initial video items found in page data (first few videos may lack IDs)")
        except Exception as e:
            logger.debug("Failed to extract initial items: %s", e)

    @staticmethod
    def _find_chrome_binary() -> str:
        """Find the real Chrome binary on macOS."""
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
        for path in candidates:
            if Path(path).exists():
                return path
        raise FileNotFoundError("Could not find Google Chrome. Install it from google.com/chrome")

    def _launch_chrome_with_debugging(self) -> subprocess.Popen:
        """Launch the user's real Chrome with remote debugging enabled."""
        chrome_bin = self._find_chrome_binary()
        chrome_data_dir = self._chrome_dir

        # Launch Chrome with remote debugging on port 9222
        proc = subprocess.Popen(
            [
                chrome_bin,
                f"--user-data-dir={chrome_data_dir}",
                "--remote-debugging-port=9222",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc

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
        self._log("Closing any existing Chrome instances...")

        # Kill existing Chrome so we can relaunch with debugging
        subprocess.run(["pkill", "-f", "Google Chrome"], capture_output=True)
        await asyncio.sleep(2)

        self._log("Launching Chrome with your account...")
        chrome_proc = self._launch_chrome_with_debugging()

        # Wait for Chrome debugging port to be ready
        for i in range(15):
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    resp = await client.get("http://127.0.0.1:9222/json/version")
                    if resp.status_code == 200:
                        break
            except Exception:
                pass
            await asyncio.sleep(1)
        else:
            self._log("Chrome failed to start with debugging port")
            chrome_proc.terminate()
            return

        self._log("Connecting to Chrome...")

        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
            page.on("response", self._handle_response)

            self._log("Navigating to TikTok FYP...")
            await page.goto("https://www.tiktok.com/foryou", wait_until="networkidle")
            await asyncio.sleep(3)

            # Extract initial video items from the page's SSR data
            # The first batch of videos is rendered server-side and won't
            # appear in XHR intercepts, so we parse them from embedded scripts
            await self._extract_initial_items(page)

            self._log("Starting scroll loop...")
            last_article_id = ""
            panel_is_open = False

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

                # 3. CHECK — should we scrape this video?
                should_scrape = (
                    comment_count >= self._threshold
                    and video_id is not None
                    and not any(v.id == video_id for v in self._videos)
                )

                if not should_scrape:
                    if comment_count >= self._threshold and not video_id:
                        self._log(f"No video ID for @{author}, skipping")
                    await page.keyboard.press("ArrowDown")
                    await asyncio.sleep(1.5)
                    continue

                # 4. SCRAPE this video
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
                panel_is_open = await self._scrape_comments(
                    page, video_id, article_id, panel_is_open
                )
                self._update_stats()

                # 5. Scroll to next video (panel stays open)
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(1.5)

            self._log("Scroll loop stopped. Closing tab...")
            await page.close()
            browser.close()

    # ------------------------------------------------------------------
    # Comment scraping — scoped to the correct article
    # ------------------------------------------------------------------

    async def _scrape_comments(
        self, page: Page, video_id: str, article_id: str,
        panel_is_open: bool,
        max_comments: int = 30,
    ) -> bool:
        """Scrape comments from the current video. Returns True if panel is open after."""
        self._intercepted_comments.clear()

        if not panel_is_open:
            # First time — open the comment panel
            opened = await self._click_comment_button_in_article(page, article_id)
            if not opened:
                self._log("Failed to open comment panel")
                return False
            await asyncio.sleep(2)
        else:
            # Panel is already open from previous video — TikTok auto-loads
            # new comments when you scroll to a new video with panel open.
            # Just wait for the new comments to arrive via XHR.
            await asyncio.sleep(2)

        # Wait for comments to load, scroll panel if needed
        stall_count = 0
        max_stalls = 2

        while stall_count < max_stalls and len(self._intercepted_comments) < max_comments:
            prev_count = len(self._intercepted_comments)

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

            if len(self._intercepted_comments) == prev_count:
                stall_count += 1
            else:
                stall_count = 0

        # Store collected comments (capped at max_comments)
        for raw in self._intercepted_comments[:max_comments]:
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

        collected = min(len(self._intercepted_comments), max_comments)
        self._log(f"Collected {collected} comments")

        # Keep the panel open — it will auto-update for the next video
        return True
