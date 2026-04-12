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
        max_comments_per_video: int = 30,
        max_videos: int = 0,
        on_log: Optional[Callable[[str], None]] = None,
        on_stats_update: Optional[Callable[[int, int, int], None]] = None,
    ) -> None:
        self._chrome_dir = chrome_user_data_dir
        self._threshold = comment_threshold
        self._max_comments = max_comments_per_video
        self._max_videos = max_videos  # 0 = unlimited
        self._on_log = on_log or (lambda msg: None)
        self._on_stats_update = on_stats_update or (lambda a, b, c: None)
        self._stop_event = asyncio.Event()
        self._videos: list[Video] = []
        self._comments: list[Comment] = []
        self._videos_scanned = 0
        self._videos_scraped = 0
        self._intercepted_video_items: list[dict] = []
        # Comments keyed by aweme_id — TikTok pre-loads them before we scroll
        self._comments_by_video: dict[str, list[dict]] = {}

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
                # Extract aweme_id from URL to key comments by video
                aweme_match = re.search(r"aweme_id=(\d+)", url)
                if not aweme_match:
                    return
                aweme_id = aweme_match.group(1)
                body = await response.json()
                comments_data = body.get("comments", [])
                if comments_data:
                    existing = self._comments_by_video.get(aweme_id, [])
                    self._comments_by_video[aweme_id] = [*existing, *comments_data]
                    self._log(
                        f"Got {len(comments_data)} comments for video {aweme_id} "
                        f"(total: {len(self._comments_by_video[aweme_id])})"
                    )
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
        profile_dir = str(Path.home() / ".ideascroller" / "profile")
        Path(profile_dir).mkdir(parents=True, exist_ok=True)

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

            # If not logged in, wait for user to log in manually
            if "login" in page.url.lower():
                self._log("Log in to TikTok in the browser window — I'll wait...")
                while "login" in page.url.lower() and not self._stop_event.is_set():
                    await asyncio.sleep(3)
                if self._stop_event.is_set():
                    await context.close()
                    return
                self._log("Logged in! Navigating to FYP...")
                await page.goto("https://www.tiktok.com/foryou", wait_until="networkidle")
                await asyncio.sleep(3)

            # Extract initial video items from SSR data
            await self._extract_initial_items(page)

            # Open comment panel ONCE — it stays open and auto-updates
            # as we scroll through videos
            self._log("Opening comment panel...")
            try:
                await page.evaluate("""() => {
                    const icon = document.querySelector('span[data-e2e="comment-icon"]');
                    if (icon) {
                        const btn = icon.closest('button') || icon.parentElement;
                        if (btn) btn.click();
                    }
                }""")
                await asyncio.sleep(2)
                self._log("Comment panel opened — it will stay open during scrolling")
            except Exception:
                self._log("Could not open comment panel — comments will be collected via XHR only")

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

                # 4. SCRAPE — panel is already open, just collect XHR comments
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

                self._log(f">>> Collecting comments for @{author}...")
                await self._collect_comments(page, video_id)
                self._update_stats()

                # Auto-stop if we've hit the max videos limit
                if self._max_videos > 0 and self._videos_scraped >= self._max_videos:
                    self._log(f"Reached max videos limit ({self._max_videos}). Stopping.")
                    break

                # 5. Scroll to next video (panel stays open)
                await page.keyboard.press("ArrowDown")
                await asyncio.sleep(1.5)

            self._log("Scroll loop stopped.")
            try:
                await context.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Comment collection — panel is already open, just wait for XHR
    # ------------------------------------------------------------------

    async def _collect_comments(
        self, page: Page, video_id: str,
    ) -> None:
        """Collect comments for a video. Comments may already be in the cache
        (TikTok pre-loads them) or may arrive shortly via XHR."""
        max_comments = self._max_comments

        # Wait briefly for comments to arrive if not already cached
        for _ in range(5):
            cached = self._comments_by_video.get(video_id, [])
            if len(cached) >= 1:
                break
            await asyncio.sleep(0.5)

        raw_comments = self._comments_by_video.get(video_id, [])

        # Store comments (capped at max_comments)
        for raw in raw_comments[:max_comments]:
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

        collected = min(len(raw_comments), max_comments)
        self._log(f"Collected {collected} comments")
