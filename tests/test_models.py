import datetime
from ideascroller.models import (
    SessionStatus, Session, Video, Comment,
    AnalysisCluster, AnalysisResult, LogEvent, SessionStats,
)


def test_session_creation():
    session = Session(threshold=300)
    assert session.id is not None
    assert session.status == SessionStatus.RUNNING
    assert session.threshold == 300
    assert session.videos_scanned == 0
    assert session.stopped_at is None


def test_video_creation():
    video = Video(id="7198206283571285294", session_id="test", author="testuser",
                  description="Test video", comment_count=500,
                  url="https://www.tiktok.com/@testuser/video/7198206283571285294")
    assert video.id == "7198206283571285294"
    assert video.comment_count == 500


def test_comment_creation():
    comment = Comment(id="cid-123", video_id="7198206283571285294",
                      text="This is so relatable", author="commenter1",
                      likes=42, reply_count=3,
                      created_at=datetime.datetime(2026, 1, 15, 10, 30, 0))
    assert comment.text == "This is so relatable"
    assert comment.likes == 42


def test_analysis_cluster():
    cluster = AnalysisCluster(theme="Meal planning is hard",
                               summary="People struggle", comment_count=342,
                               video_count=5, potential="HIGH",
                               app_idea="Fridge app", sample_comments=["c1", "c2"])
    assert cluster.potential == "HIGH"
    assert len(cluster.sample_comments) == 2


def test_analysis_result():
    cluster = AnalysisCluster(theme="Test", summary="Summary", comment_count=10,
                               video_count=1, potential="LOW", app_idea="Idea",
                               sample_comments=["c1"])
    result = AnalysisResult(session_id="test", clusters=[cluster])
    assert result.id is not None
    assert len(result.clusters) == 1


def test_log_event():
    event = LogEvent(message="Started scrolling")
    assert event.timestamp is not None


def test_session_stats():
    stats = SessionStats(videos_scanned=10, videos_scraped=3, total_comments=450)
    assert stats.videos_scanned == 10
