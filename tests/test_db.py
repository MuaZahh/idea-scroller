import pytest
from ideascroller.db import Database
from ideascroller.models import Session, Video, Comment, AnalysisResult, AnalysisCluster


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()


async def test_initialize_creates_tables(db):
    tables = await db.list_tables()
    assert "sessions" in tables
    assert "videos" in tables
    assert "comments" in tables
    assert "analyses" in tables


async def test_save_and_get_session(db):
    session = Session(threshold=500)
    await db.save_session(session)
    retrieved = await db.get_session(session.id)
    assert retrieved is not None
    assert retrieved.id == session.id
    assert retrieved.threshold == 500
    assert retrieved.status.value == "running"


async def test_update_session(db):
    session = Session(threshold=300)
    await db.save_session(session)
    updated = session.model_copy(update={"videos_scanned": 10, "videos_scraped": 3, "total_comments": 450})
    await db.update_session(updated)
    retrieved = await db.get_session(session.id)
    assert retrieved.videos_scanned == 10
    assert retrieved.videos_scraped == 3
    assert retrieved.total_comments == 450


async def test_save_and_get_video(db):
    session = Session(threshold=300)
    await db.save_session(session)
    video = Video(id="vid-123", session_id=session.id, author="testuser",
                  description="Test video", comment_count=500,
                  url="https://tiktok.com/@testuser/video/vid-123")
    await db.save_video(video)
    videos = await db.get_videos(session.id)
    assert len(videos) == 1
    assert videos[0].id == "vid-123"


async def test_save_and_get_comments(db):
    session = Session(threshold=300)
    await db.save_session(session)
    video = Video(id="vid-123", session_id=session.id, author="testuser",
                  description="Test", comment_count=2, url="https://tiktok.com/vid-123")
    await db.save_video(video)
    comments = [
        Comment(id="c1", video_id="vid-123", text="Great idea", author="user1", likes=5, reply_count=0),
        Comment(id="c2", video_id="vid-123", text="Me too!", author="user2", likes=12, reply_count=1),
    ]
    await db.save_comments(comments)
    retrieved = await db.get_comments("vid-123")
    assert len(retrieved) == 2
    assert {c.text for c in retrieved} == {"Great idea", "Me too!"}


async def test_get_all_session_comments(db):
    session = Session(threshold=300)
    await db.save_session(session)
    for vid_num in range(2):
        vid_id = f"vid-{vid_num}"
        video = Video(id=vid_id, session_id=session.id, author="user",
                      description="desc", comment_count=1, url=f"https://tiktok.com/{vid_id}")
        await db.save_video(video)
        await db.save_comments([
            Comment(id=f"c-{vid_num}", video_id=vid_id, text=f"comment {vid_num}", author="a", likes=0, reply_count=0),
        ])
    all_comments = await db.get_session_comments(session.id)
    assert len(all_comments) == 2


async def test_save_and_get_analysis(db):
    session = Session(threshold=300)
    await db.save_session(session)
    cluster = AnalysisCluster(theme="Test", summary="Summary", comment_count=10,
                               video_count=1, potential="HIGH", app_idea="Test app",
                               sample_comments=["c1"])
    result = AnalysisResult(session_id=session.id, clusters=[cluster], raw_response="raw")
    await db.save_analysis(result)
    retrieved = await db.get_analysis(session.id)
    assert retrieved is not None
    assert len(retrieved.clusters) == 1
    assert retrieved.clusters[0].theme == "Test"
