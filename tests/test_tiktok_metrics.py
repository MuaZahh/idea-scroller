"""Tests for validators.tiktok_metrics."""

import pytest

from ideascroller.models import Video
from ideascroller.validators.tiktok_metrics import (
    VideoMetrics,
    compute_virality_score,
    enrich_videos_with_metrics,
    extract_metrics,
)


def _make_video(video_id: str, comment_count: int = 0) -> Video:
    return Video(
        id=video_id,
        session_id="sess-1",
        author="bob",
        description="desc",
        comment_count=comment_count,
        url=f"https://tiktok.com/@bob/video/{video_id}",
    )


@pytest.mark.unit
def test_extract_metrics_from_stats_dict():
    raw = {
        "id": "7123456789",
        "author": {"uniqueId": "bob"},
        "desc": "look at my plant",
        "stats": {
            "diggCount": 48200,
            "shareCount": 1200,
            "commentCount": 5400,
            "playCount": 1_200_000,
            "collectCount": 800,
        },
    }
    metrics = extract_metrics(raw)
    assert metrics is not None
    assert metrics.video_id == "7123456789"
    assert metrics.view_count == 1_200_000
    assert metrics.like_count == 48200
    assert metrics.comment_count == 5400
    assert metrics.share_count == 1200
    assert metrics.save_count == 800
    assert 0.0 < metrics.virality_score <= 1.0


@pytest.mark.unit
def test_extract_metrics_from_stats_v2_string_values():
    raw = {
        "id": "999",
        "statsV2": {
            "diggCount": "48200",
            "shareCount": "1200",
            "commentCount": "5400",
            "playCount": "1200000",
            "collectCount": "800",
        },
    }
    metrics = extract_metrics(raw)
    assert metrics is not None
    assert metrics.view_count == 1_200_000
    assert metrics.like_count == 48200
    assert metrics.comment_count == 5400
    assert metrics.share_count == 1200
    assert metrics.save_count == 800


@pytest.mark.unit
def test_extract_metrics_from_top_level_counters():
    raw = {
        "id": "top-level-1",
        "playCount": 500,
        "diggCount": 10,
        "commentCount": 4,
        "shareCount": 2,
        "collectCount": 1,
    }
    metrics = extract_metrics(raw)
    assert metrics is not None
    assert metrics.view_count == 500
    assert metrics.like_count == 10
    assert metrics.comment_count == 4
    assert metrics.share_count == 2
    assert metrics.save_count == 1


@pytest.mark.unit
def test_extract_metrics_prefers_stats_over_stats_v2_over_top_level():
    raw = {
        "id": "abc",
        "stats": {"playCount": 1000},
        "statsV2": {"playCount": "2000"},
        "playCount": 3000,
    }
    metrics = extract_metrics(raw)
    assert metrics is not None
    assert metrics.view_count == 1000


@pytest.mark.unit
def test_extract_metrics_returns_none_without_id():
    assert extract_metrics({"stats": {"playCount": 100}}) is None
    assert extract_metrics({"id": ""}) is None
    assert extract_metrics({"id": None}) is None


@pytest.mark.unit
def test_extract_metrics_returns_none_for_non_dict():
    assert extract_metrics(None) is None  # type: ignore[arg-type]
    assert extract_metrics("notadict") is None  # type: ignore[arg-type]
    assert extract_metrics(["a", "b"]) is None  # type: ignore[arg-type]


@pytest.mark.unit
def test_extract_metrics_defaults_missing_counters_to_zero():
    raw = {"id": "sparse", "stats": {"diggCount": 50}}
    metrics = extract_metrics(raw)
    assert metrics is not None
    assert metrics.like_count == 50
    assert metrics.view_count == 0
    assert metrics.comment_count == 0
    assert metrics.share_count == 0
    assert metrics.save_count == 0
    assert metrics.virality_score == 0.0


@pytest.mark.unit
def test_extract_metrics_handles_empty_stats():
    raw = {"id": "empty", "stats": {}}
    metrics = extract_metrics(raw)
    assert metrics is not None
    assert metrics.view_count == 0
    assert metrics.virality_score == 0.0


@pytest.mark.unit
def test_compute_virality_score_zero_views():
    assert compute_virality_score(0, 0, 0, 0) == 0.0
    assert compute_virality_score(0, 1000, 1000, 1000) == 0.0


@pytest.mark.unit
def test_compute_virality_score_100k_views_about_half():
    score = compute_virality_score(100_000, 0, 0, 0)
    assert score == pytest.approx(0.5, abs=0.01)


@pytest.mark.unit
def test_compute_virality_score_1m_views():
    score = compute_virality_score(1_000_000, 0, 0, 0)
    assert score == pytest.approx(0.75, abs=0.01)


@pytest.mark.unit
def test_compute_virality_score_10m_views_maxes():
    assert compute_virality_score(10_000_000, 0, 0, 0) == pytest.approx(1.0)
    assert compute_virality_score(50_000_000, 0, 0, 0) == pytest.approx(1.0)


@pytest.mark.unit
def test_compute_virality_score_below_100k_scales_down():
    low = compute_virality_score(10_000, 0, 0, 0)
    assert 0.0 < low < 0.5
    lower = compute_virality_score(1_000, 0, 0, 0)
    assert 0.0 < lower < low


@pytest.mark.unit
def test_compute_virality_score_engagement_bonus_small():
    # 100k views, rate = (7000 + 500*3 + 0) / 100000 = 0.085 -> +0.1 bonus.
    base = compute_virality_score(100_000, 0, 0, 0)
    boosted = compute_virality_score(100_000, 7000, 500, 0)
    assert boosted > base
    assert boosted == pytest.approx(base + 0.1, abs=0.01)


@pytest.mark.unit
def test_compute_virality_score_engagement_bonus_large():
    # 100k views, rate = (10000 + 1000*3 + 500*5) / 100000 = 0.155 -> +0.2 bonus.
    base = compute_virality_score(100_000, 0, 0, 0)
    boosted = compute_virality_score(100_000, 10_000, 1000, 500)
    assert boosted == pytest.approx(base + 0.2, abs=0.01)


@pytest.mark.unit
def test_compute_virality_score_capped_at_one():
    score = compute_virality_score(
        100_000_000, 50_000_000, 10_000_000, 5_000_000
    )
    assert score == 1.0


@pytest.mark.unit
def test_enrich_videos_matches_by_id():
    videos = [_make_video("v1", 5), _make_video("v2", 10)]
    raw_items = [
        {
            "id": "v1",
            "stats": {
                "playCount": 500_000,
                "diggCount": 10_000,
                "commentCount": 5,
                "shareCount": 100,
                "collectCount": 50,
            },
        },
        {
            "id": "v2",
            "stats": {
                "playCount": 2_000_000,
                "diggCount": 50_000,
                "commentCount": 10,
                "shareCount": 500,
                "collectCount": 200,
            },
        },
    ]
    result = enrich_videos_with_metrics(videos, raw_items)

    assert set(result.keys()) == {"v1", "v2"}
    assert result["v1"].view_count == 500_000
    assert result["v2"].view_count == 2_000_000
    assert result["v1"].virality_score > 0
    assert result["v2"].virality_score > result["v1"].virality_score


@pytest.mark.unit
def test_enrich_videos_fills_zero_stub_for_unmatched():
    videos = [_make_video("matched", 3), _make_video("orphan", 99)]
    raw_items = [
        {"id": "matched", "stats": {"playCount": 1_000_000}},
        {"id": "extra-not-in-videos", "stats": {"playCount": 5_000_000}},
    ]
    result = enrich_videos_with_metrics(videos, raw_items)

    assert set(result.keys()) == {"matched", "orphan"}
    matched = result["matched"]
    assert matched.view_count == 1_000_000

    orphan = result["orphan"]
    assert orphan.video_id == "orphan"
    assert orphan.view_count == 0
    assert orphan.like_count == 0
    assert orphan.comment_count == 99  # preserved from Video
    assert orphan.share_count == 0
    assert orphan.save_count == 0
    assert orphan.virality_score == 0.0


@pytest.mark.unit
def test_enrich_videos_handles_empty_inputs():
    assert enrich_videos_with_metrics([], []) == {}
    assert enrich_videos_with_metrics([], [{"id": "x"}]) == {}


@pytest.mark.unit
def test_enrich_videos_skips_malformed_raw_items():
    videos = [_make_video("v1", 0)]
    raw_items = [
        None,  # type: ignore[list-item]
        "not-a-dict",  # type: ignore[list-item]
        {"no_id": "oops"},
        {"id": "v1", "stats": {"playCount": 100_000}},
    ]
    result = enrich_videos_with_metrics(videos, raw_items)  # type: ignore[arg-type]
    assert result["v1"].view_count == 100_000


@pytest.mark.unit
def test_video_metrics_is_frozen():
    m = VideoMetrics("x", 1, 2, 3, 4, 5, 0.5)
    with pytest.raises(Exception):
        m.view_count = 999  # type: ignore[misc]
