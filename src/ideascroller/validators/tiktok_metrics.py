"""Virality metrics extraction from TikTok's intercepted item-list XHR.

Pure functions — no I/O, no global state. The scraper integration layer
is responsible for wiring these into the pipeline.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ideascroller.models import Video


# TikTok uses these keys inside the "stats" (and "statsV2") sub-object.
_VIEW_KEYS = ("playCount", "viewCount")
_LIKE_KEYS = ("diggCount", "likeCount")
_COMMENT_KEYS = ("commentCount",)
_SHARE_KEYS = ("shareCount",)
_SAVE_KEYS = ("collectCount", "saveCount")


@dataclass(frozen=True)
class VideoMetrics:
    """Immutable view/engagement snapshot for a single TikTok video."""

    video_id: str
    view_count: int
    like_count: int
    comment_count: int
    share_count: int
    save_count: int
    virality_score: float  # 0.0–1.0 composite


def _coerce_int(value: Any) -> int:
    """Best-effort int conversion. Returns 0 for None, empty, or junk."""
    if value is None:
        return 0
    if isinstance(value, bool):  # bool is an int subclass — reject it
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else 0
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return 0
        try:
            return int(stripped)
        except ValueError:
            try:
                return int(float(stripped))
            except ValueError:
                return 0
    return 0


def _first_nonzero(source: dict, keys: tuple[str, ...]) -> int:
    """Return the first coercible non-zero value for any key, else 0."""
    for key in keys:
        if key in source:
            value = _coerce_int(source[key])
            if value:
                return value
    # Fallback: return whatever the first present key coerces to, even if 0.
    for key in keys:
        if key in source:
            return _coerce_int(source[key])
    return 0


def _pick_counter(raw_item: dict, keys: tuple[str, ...]) -> int:
    """Find a counter across stats → statsV2 → top-level, in that order."""
    stats = raw_item.get("stats")
    if isinstance(stats, dict):
        value = _first_nonzero(stats, keys)
        if value:
            return value
    stats_v2 = raw_item.get("statsV2")
    if isinstance(stats_v2, dict):
        value = _first_nonzero(stats_v2, keys)
        if value:
            return value
    # Top-level legacy fallback.
    return _first_nonzero(raw_item, keys)


def compute_virality_score(
    views: int, likes: int, comments: int, shares: int
) -> float:
    """Composite virality score in [0.0, 1.0].

    Rules:
    - 0 views returns 0.0.
    - Logarithmic view anchors: 100K views -> 0.5, 1M -> 0.75, 10M+ -> 1.0.
      Below 100K scales proportionally from 0 up to 0.5.
    - Engagement-rate bonus on top of the base view score:
        rate = (likes + comments*3 + shares*5) / views
        rate > 0.05 adds +0.1
        rate > 0.10 adds +0.2   (replaces the +0.1, not additive)
      Final score is capped at 1.0.
    """
    if views <= 0:
        return 0.0

    log_views = math.log10(views)
    if views >= 10_000_000:
        base = 1.0
    elif views >= 1_000_000:
        base = 0.75 + (log_views - 6.0) * 0.25  # 1M -> 0.75, 10M -> 1.0
    elif views >= 100_000:
        base = 0.5 + (log_views - 5.0) * 0.25  # 100K -> 0.5, 1M -> 0.75
    else:
        base = max(0.0, log_views * 0.1)  # 1 view -> 0.0, 100K -> 0.5

    weighted_engagement = likes + comments * 3 + shares * 5
    rate = weighted_engagement / views
    if rate > 0.10:
        bonus = 0.2
    elif rate > 0.05:
        bonus = 0.1
    else:
        bonus = 0.0

    return min(1.0, base + bonus)


def extract_metrics(raw_item: dict) -> VideoMetrics | None:
    """Extract a VideoMetrics from a single TikTok item-list item.

    Returns None if the item is malformed or missing an id. Missing
    counters default to 0 — never raises on incomplete payloads.
    """
    if not isinstance(raw_item, dict):
        return None

    video_id = raw_item.get("id")
    if not isinstance(video_id, str) or not video_id.strip():
        return None

    views = _pick_counter(raw_item, _VIEW_KEYS)
    likes = _pick_counter(raw_item, _LIKE_KEYS)
    comments = _pick_counter(raw_item, _COMMENT_KEYS)
    shares = _pick_counter(raw_item, _SHARE_KEYS)
    saves = _pick_counter(raw_item, _SAVE_KEYS)

    return VideoMetrics(
        video_id=video_id,
        view_count=views,
        like_count=likes,
        comment_count=comments,
        share_count=shares,
        save_count=saves,
        virality_score=compute_virality_score(views, likes, comments, shares),
    )


def enrich_videos_with_metrics(
    videos: list["Video"],
    raw_items: list[dict],
) -> dict[str, VideoMetrics]:
    """Build {video_id: VideoMetrics} for the given videos.

    Videos without a matching raw_item get a zero-filled stub that still
    carries the known comment_count from the Video object.
    """
    extracted: dict[str, VideoMetrics] = {}
    for item in raw_items:
        metrics = extract_metrics(item) if isinstance(item, dict) else None
        if metrics is not None:
            extracted[metrics.video_id] = metrics

    result: dict[str, VideoMetrics] = {}
    for video in videos:
        if video.id in extracted:
            result[video.id] = extracted[video.id]
        else:
            result[video.id] = VideoMetrics(
                video_id=video.id,
                view_count=0,
                like_count=0,
                comment_count=video.comment_count,
                share_count=0,
                save_count=0,
                virality_score=0.0,
            )
    return result
