# IdeaScroller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a tool that scrolls TikTok FYP, scrapes comments from high-engagement videos, and uses Claude to cluster pain points into app ideas.

**Architecture:** FastAPI server orchestrates a Playwright browser that scrolls TikTok FYP and intercepts XHR responses for video metadata and comments. After the user stops the session, all comments are sent to Claude Sonnet for thematic clustering and app idea generation. Results displayed in a minimal single-page GUI connected via WebSocket.

**Tech Stack:** Python 3.12+, FastAPI, Playwright (Python), Anthropic SDK, SQLite (aiosqlite), uvicorn, plain HTML/CSS/JS

---

## File Structure

```
tiktok-scroller/
├── pyproject.toml              # Project metadata, dependencies
├── .env.example                # Template for required env vars
├── .gitignore                  # Python, SQLite, .env, .superpowers
├── src/
│   └── ideascroller/
│       ├── __init__.py         # Package init, version
│       ├── __main__.py         # CLI entry point
│       ├── config.py           # Settings from env vars with defaults
│       ├── models.py           # Pydantic models for API, DB, analysis
│       ├── db.py               # SQLite schema, CRUD operations
│       ├── scraper.py          # Playwright engine — scroll, intercept, scrape
│       ├── analyzer.py         # LLM analysis with Anthropic SDK
│       ├── server.py           # FastAPI app, routes, WebSocket
│       └── static/
│           └── index.html      # Single-page GUI
├── tests/
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_models.py
│   ├── test_db.py
│   ├── test_analyzer.py
│   ├── test_scraper_utils.py
│   ├── test_server.py
│   └── test_integration.py
└── docs/
```

---

### Task 1: Project Scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `src/ideascroller/__init__.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "ideascroller"
version = "0.1.0"
description = "TikTok FYP pain point discovery tool"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "playwright>=1.49.0",
    "anthropic>=0.40.0",
    "aiosqlite>=0.20.0",
    "pydantic>=2.10.0",
    "pydantic-settings>=2.6.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
    "httpx>=0.28.0",
    "pytest-cov>=6.0.0",
]

[project.scripts]
ideascroller = "ideascroller.__main__:main"

[build-system]
requires = ["setuptools>=75.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create .env.example**

```
ANTHROPIC_API_KEY=sk-ant-...
CHROME_USER_DATA_DIR=
COMMENT_THRESHOLD=300
HOST=127.0.0.1
PORT=8000
```

- [ ] **Step 3: Create .gitignore**

```
__pycache__/
*.pyc
.env
*.db
.superpowers/
dist/
*.egg-info/
.pytest_cache/
```

- [ ] **Step 4: Create src/ideascroller/__init__.py**

```python
"""IdeaScroller — TikTok pain point discovery tool."""

__version__ = "0.1.0"
```

- [ ] **Step 5: Create tests/__init__.py**

Empty file.

- [ ] **Step 6: Install dependencies and Playwright**

```bash
cd "/Users/zaheemnazoordeen/tiktok scroller"
pip install -e ".[dev]"
playwright install chromium
```

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .env.example .gitignore src/ideascroller/__init__.py tests/__init__.py
git commit -m "chore: scaffold IdeaScroller project with dependencies"
```

---

### Task 2: Configuration

**Files:**
- Create: `src/ideascroller/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import os
from unittest.mock import patch


def test_default_settings():
    """Settings should have sensible defaults when no env vars are set."""
    with patch.dict(os.environ, {}, clear=True):
        from importlib import reload
        import ideascroller.config as config_module
        reload(config_module)
        settings = config_module.Settings()

    assert settings.comment_threshold == 300
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.anthropic_api_key == ""
    assert settings.chrome_user_data_dir == ""


def test_settings_from_env():
    """Settings should read from environment variables."""
    env = {
        "ANTHROPIC_API_KEY": "sk-test-key",
        "CHROME_USER_DATA_DIR": "/path/to/chrome",
        "COMMENT_THRESHOLD": "500",
        "HOST": "0.0.0.0",
        "PORT": "9000",
    }
    with patch.dict(os.environ, env, clear=True):
        from importlib import reload
        import ideascroller.config as config_module
        reload(config_module)
        settings = config_module.Settings()

    assert settings.anthropic_api_key == "sk-test-key"
    assert settings.chrome_user_data_dir == "/path/to/chrome"
    assert settings.comment_threshold == 500
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000


def test_default_chrome_path_macos():
    """get_chrome_user_data_dir should return macOS default when not configured."""
    from ideascroller.config import get_chrome_user_data_dir
    import platform

    with patch.dict(os.environ, {}, clear=True):
        from importlib import reload
        import ideascroller.config as config_module
        reload(config_module)
        settings = config_module.Settings()

    path = get_chrome_user_data_dir(settings)
    if platform.system() == "Darwin":
        assert "Library/Application Support/Google/Chrome" in path
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_config.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'ideascroller.config'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ideascroller/config.py
"""Configuration loaded from environment variables."""

import platform
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings with environment variable support."""

    anthropic_api_key: str = ""
    chrome_user_data_dir: str = ""
    comment_threshold: int = 300
    host: str = "127.0.0.1"
    port: int = 8000

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


def get_chrome_user_data_dir(settings: Settings) -> str:
    """Return Chrome user data directory, using platform default if not configured."""
    if settings.chrome_user_data_dir:
        return settings.chrome_user_data_dir

    system = platform.system()
    home = Path.home()

    if system == "Darwin":
        return str(home / "Library" / "Application Support" / "Google" / "Chrome")
    elif system == "Linux":
        return str(home / ".config" / "google-chrome")
    else:
        return str(home / "AppData" / "Local" / "Google" / "Chrome" / "User Data")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_config.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ideascroller/config.py tests/test_config.py
git commit -m "feat: add config module with env var support"
```

---

### Task 3: Pydantic Models

**Files:**
- Create: `src/ideascroller/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models.py
import datetime
from ideascroller.models import (
    SessionStatus,
    Session,
    Video,
    Comment,
    AnalysisCluster,
    AnalysisResult,
    LogEvent,
    SessionStats,
)


def test_session_creation():
    session = Session(threshold=300)
    assert session.id is not None
    assert session.status == SessionStatus.RUNNING
    assert session.threshold == 300
    assert session.videos_scanned == 0
    assert session.videos_scraped == 0
    assert session.total_comments == 0
    assert session.started_at is not None
    assert session.stopped_at is None


def test_video_creation():
    video = Video(
        id="7198206283571285294",
        session_id="test-session",
        author="testuser",
        description="Test video",
        comment_count=500,
        url="https://www.tiktok.com/@testuser/video/7198206283571285294",
    )
    assert video.id == "7198206283571285294"
    assert video.comment_count == 500


def test_comment_creation():
    comment = Comment(
        id="cid-123",
        video_id="7198206283571285294",
        text="This is so relatable",
        author="commenter1",
        likes=42,
        reply_count=3,
        created_at=datetime.datetime(2026, 1, 15, 10, 30, 0),
    )
    assert comment.text == "This is so relatable"
    assert comment.likes == 42


def test_analysis_cluster():
    cluster = AnalysisCluster(
        theme="Meal planning is hard",
        summary="People struggle with deciding what to cook",
        comment_count=342,
        video_count=5,
        potential="HIGH",
        app_idea="Fridge-to-meal suggestion app",
        sample_comments=["comment 1", "comment 2"],
    )
    assert cluster.potential == "HIGH"
    assert len(cluster.sample_comments) == 2


def test_analysis_result():
    cluster = AnalysisCluster(
        theme="Test",
        summary="Test summary",
        comment_count=10,
        video_count=1,
        potential="LOW",
        app_idea="Test idea",
        sample_comments=["c1"],
    )
    result = AnalysisResult(session_id="test-session", clusters=[cluster])
    assert result.id is not None
    assert len(result.clusters) == 1


def test_log_event():
    event = LogEvent(message="Started scrolling")
    assert event.timestamp is not None
    assert event.message == "Started scrolling"


def test_session_stats():
    stats = SessionStats(videos_scanned=10, videos_scraped=3, total_comments=450)
    assert stats.videos_scanned == 10
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_models.py -v
```
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ideascroller/models.py
"""Pydantic models for API, database, and analysis."""

import datetime
import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    RUNNING = "running"
    ANALYZING = "analyzing"
    COMPLETE = "complete"
    ERROR = "error"


class Session(BaseModel):
    """A scrolling session."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime.datetime = Field(default_factory=datetime.datetime.now)
    stopped_at: Optional[datetime.datetime] = None
    status: SessionStatus = SessionStatus.RUNNING
    threshold: int = 300
    videos_scanned: int = 0
    videos_scraped: int = 0
    total_comments: int = 0


class Video(BaseModel):
    """A TikTok video that met the comment threshold."""

    id: str
    session_id: str
    author: str
    description: str
    comment_count: int
    url: str


class Comment(BaseModel):
    """A single TikTok comment."""

    id: str
    video_id: str
    text: str
    author: str
    likes: int = 0
    reply_count: int = 0
    created_at: Optional[datetime.datetime] = None


class AnalysisCluster(BaseModel):
    """A themed cluster of pain points identified by the LLM."""

    theme: str
    summary: str
    comment_count: int
    video_count: int
    potential: str  # HIGH, MEDIUM, LOW
    app_idea: str
    sample_comments: list[str]


class AnalysisResult(BaseModel):
    """Full analysis output for a session."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    clusters: list[AnalysisCluster]
    raw_response: str = ""
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.now)


class LogEvent(BaseModel):
    """A real-time log event for the GUI."""

    timestamp: datetime.datetime = Field(default_factory=datetime.datetime.now)
    message: str


class SessionStats(BaseModel):
    """Live session counters for the GUI."""

    videos_scanned: int = 0
    videos_scraped: int = 0
    total_comments: int = 0
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_models.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ideascroller/models.py tests/test_models.py
git commit -m "feat: add Pydantic models for sessions, videos, comments, analysis"
```

---

### Task 4: Database Layer

**Files:**
- Create: `src/ideascroller/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py
import pytest
from ideascroller.db import Database
from ideascroller.models import Session, Video, Comment, AnalysisResult, AnalysisCluster


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()


async def test_initialize_creates_tables(db: Database):
    """Database initialization should create all required tables."""
    tables = await db.list_tables()
    assert "sessions" in tables
    assert "videos" in tables
    assert "comments" in tables
    assert "analyses" in tables


async def test_save_and_get_session(db: Database):
    session = Session(threshold=500)
    await db.save_session(session)

    retrieved = await db.get_session(session.id)
    assert retrieved is not None
    assert retrieved.id == session.id
    assert retrieved.threshold == 500
    assert retrieved.status.value == "running"


async def test_update_session(db: Database):
    session = Session(threshold=300)
    await db.save_session(session)

    updated = session.model_copy(
        update={"videos_scanned": 10, "videos_scraped": 3, "total_comments": 450}
    )
    await db.update_session(updated)

    retrieved = await db.get_session(session.id)
    assert retrieved.videos_scanned == 10
    assert retrieved.videos_scraped == 3
    assert retrieved.total_comments == 450


async def test_save_and_get_video(db: Database):
    session = Session(threshold=300)
    await db.save_session(session)

    video = Video(
        id="vid-123",
        session_id=session.id,
        author="testuser",
        description="Test video",
        comment_count=500,
        url="https://tiktok.com/@testuser/video/vid-123",
    )
    await db.save_video(video)

    videos = await db.get_videos(session.id)
    assert len(videos) == 1
    assert videos[0].id == "vid-123"


async def test_save_and_get_comments(db: Database):
    session = Session(threshold=300)
    await db.save_session(session)
    video = Video(
        id="vid-123",
        session_id=session.id,
        author="testuser",
        description="Test",
        comment_count=2,
        url="https://tiktok.com/@testuser/video/vid-123",
    )
    await db.save_video(video)

    comments = [
        Comment(id="c1", video_id="vid-123", text="Great idea", author="user1", likes=5, reply_count=0),
        Comment(id="c2", video_id="vid-123", text="Me too!", author="user2", likes=12, reply_count=1),
    ]
    await db.save_comments(comments)

    retrieved = await db.get_comments("vid-123")
    assert len(retrieved) == 2
    assert {c.text for c in retrieved} == {"Great idea", "Me too!"}


async def test_get_all_session_comments(db: Database):
    """get_session_comments should return comments across all videos in a session."""
    session = Session(threshold=300)
    await db.save_session(session)

    for vid_num in range(2):
        vid_id = f"vid-{vid_num}"
        video = Video(
            id=vid_id, session_id=session.id, author="user",
            description="desc", comment_count=1, url=f"https://tiktok.com/video/{vid_id}",
        )
        await db.save_video(video)
        await db.save_comments([
            Comment(id=f"c-{vid_num}", video_id=vid_id, text=f"comment {vid_num}", author="a", likes=0, reply_count=0),
        ])

    all_comments = await db.get_session_comments(session.id)
    assert len(all_comments) == 2


async def test_save_and_get_analysis(db: Database):
    session = Session(threshold=300)
    await db.save_session(session)

    cluster = AnalysisCluster(
        theme="Test", summary="Summary", comment_count=10,
        video_count=1, potential="HIGH", app_idea="Test app",
        sample_comments=["c1"],
    )
    result = AnalysisResult(session_id=session.id, clusters=[cluster], raw_response="raw")
    await db.save_analysis(result)

    retrieved = await db.get_analysis(session.id)
    assert retrieved is not None
    assert len(retrieved.clusters) == 1
    assert retrieved.clusters[0].theme == "Test"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_db.py -v
```
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ideascroller/db.py
"""SQLite database layer for session, video, comment, and analysis storage."""

import datetime
import json
from typing import Optional

import aiosqlite

from ideascroller.models import (
    AnalysisCluster,
    AnalysisResult,
    Comment,
    Session,
    SessionStatus,
    Video,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    stopped_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    threshold INTEGER NOT NULL DEFAULT 300,
    videos_scanned INTEGER NOT NULL DEFAULT 0,
    videos_scraped INTEGER NOT NULL DEFAULT 0,
    total_comments INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS videos (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    author TEXT NOT NULL,
    description TEXT NOT NULL,
    comment_count INTEGER NOT NULL,
    url TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS comments (
    id TEXT PRIMARY KEY,
    video_id TEXT NOT NULL REFERENCES videos(id),
    text TEXT NOT NULL,
    author TEXT NOT NULL,
    likes INTEGER NOT NULL DEFAULT 0,
    reply_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS analyses (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    clusters TEXT NOT NULL,
    raw_response TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""


class Database:
    """Async SQLite database wrapper."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    async def initialize(self) -> None:
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def list_tables(self) -> list[str]:
        cursor = await self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        rows = await cursor.fetchall()
        return [row["name"] for row in rows]

    async def save_session(self, session: Session) -> None:
        await self._conn.execute(
            """INSERT INTO sessions (id, started_at, stopped_at, status, threshold,
               videos_scanned, videos_scraped, total_comments)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session.id,
                session.started_at.isoformat(),
                session.stopped_at.isoformat() if session.stopped_at else None,
                session.status.value,
                session.threshold,
                session.videos_scanned,
                session.videos_scraped,
                session.total_comments,
            ),
        )
        await self._conn.commit()

    async def get_session(self, session_id: str) -> Optional[Session]:
        cursor = await self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return Session(
            id=row["id"],
            started_at=datetime.datetime.fromisoformat(row["started_at"]),
            stopped_at=(
                datetime.datetime.fromisoformat(row["stopped_at"])
                if row["stopped_at"]
                else None
            ),
            status=SessionStatus(row["status"]),
            threshold=row["threshold"],
            videos_scanned=row["videos_scanned"],
            videos_scraped=row["videos_scraped"],
            total_comments=row["total_comments"],
        )

    async def update_session(self, session: Session) -> None:
        await self._conn.execute(
            """UPDATE sessions SET stopped_at=?, status=?, videos_scanned=?,
               videos_scraped=?, total_comments=? WHERE id=?""",
            (
                session.stopped_at.isoformat() if session.stopped_at else None,
                session.status.value,
                session.videos_scanned,
                session.videos_scraped,
                session.total_comments,
                session.id,
            ),
        )
        await self._conn.commit()

    async def save_video(self, video: Video) -> None:
        await self._conn.execute(
            """INSERT OR IGNORE INTO videos (id, session_id, author, description, comment_count, url)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (video.id, video.session_id, video.author, video.description, video.comment_count, video.url),
        )
        await self._conn.commit()

    async def get_videos(self, session_id: str) -> list[Video]:
        cursor = await self._conn.execute(
            "SELECT * FROM videos WHERE session_id = ?", (session_id,)
        )
        rows = await cursor.fetchall()
        return [
            Video(
                id=row["id"], session_id=row["session_id"], author=row["author"],
                description=row["description"], comment_count=row["comment_count"], url=row["url"],
            )
            for row in rows
        ]

    async def save_comments(self, comments: list[Comment]) -> None:
        await self._conn.executemany(
            """INSERT OR IGNORE INTO comments (id, video_id, text, author, likes, reply_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [
                (c.id, c.video_id, c.text, c.author, c.likes, c.reply_count,
                 c.created_at.isoformat() if c.created_at else None)
                for c in comments
            ],
        )
        await self._conn.commit()

    async def get_comments(self, video_id: str) -> list[Comment]:
        cursor = await self._conn.execute(
            "SELECT * FROM comments WHERE video_id = ?", (video_id,)
        )
        rows = await cursor.fetchall()
        return [
            Comment(
                id=row["id"], video_id=row["video_id"], text=row["text"],
                author=row["author"], likes=row["likes"], reply_count=row["reply_count"],
                created_at=(datetime.datetime.fromisoformat(row["created_at"]) if row["created_at"] else None),
            )
            for row in rows
        ]

    async def get_session_comments(self, session_id: str) -> list[Comment]:
        cursor = await self._conn.execute(
            """SELECT c.* FROM comments c
               JOIN videos v ON c.video_id = v.id
               WHERE v.session_id = ?""",
            (session_id,),
        )
        rows = await cursor.fetchall()
        return [
            Comment(
                id=row["id"], video_id=row["video_id"], text=row["text"],
                author=row["author"], likes=row["likes"], reply_count=row["reply_count"],
                created_at=(datetime.datetime.fromisoformat(row["created_at"]) if row["created_at"] else None),
            )
            for row in rows
        ]

    async def save_analysis(self, result: AnalysisResult) -> None:
        clusters_json = json.dumps([c.model_dump() for c in result.clusters])
        await self._conn.execute(
            """INSERT INTO analyses (id, session_id, clusters, raw_response, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (result.id, result.session_id, clusters_json, result.raw_response, result.created_at.isoformat()),
        )
        await self._conn.commit()

    async def get_analysis(self, session_id: str) -> Optional[AnalysisResult]:
        cursor = await self._conn.execute(
            "SELECT * FROM analyses WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        clusters = [AnalysisCluster(**c) for c in json.loads(row["clusters"])]
        return AnalysisResult(
            id=row["id"], session_id=row["session_id"], clusters=clusters,
            raw_response=row["raw_response"],
            created_at=datetime.datetime.fromisoformat(row["created_at"]),
        )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_db.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ideascroller/db.py tests/test_db.py
git commit -m "feat: add SQLite database layer with async CRUD operations"
```

---

### Task 5: LLM Analyzer

**Files:**
- Create: `src/ideascroller/analyzer.py`
- Create: `tests/test_analyzer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_analyzer.py
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from ideascroller.analyzer import Analyzer, build_analysis_prompt
from ideascroller.models import Comment, Video


def test_build_analysis_prompt_groups_by_video():
    """Prompt should group comments under their video descriptions."""
    videos = [
        Video(id="v1", session_id="s1", author="user1", description="Cooking tips",
              comment_count=350, url="https://tiktok.com/v1"),
    ]
    comments = [
        Comment(id="c1", video_id="v1", text="I hate meal planning", author="a", likes=50, reply_count=0),
        Comment(id="c2", video_id="v1", text="Same, so overwhelming", author="b", likes=30, reply_count=0),
    ]

    prompt = build_analysis_prompt(videos, comments)
    assert "Cooking tips" in prompt
    assert "I hate meal planning" in prompt
    assert "Same, so overwhelming" in prompt
    assert "50 likes" in prompt


def test_build_analysis_prompt_empty_comments():
    """Prompt should handle empty comment list gracefully."""
    prompt = build_analysis_prompt([], [])
    assert "no" in prompt.lower()


async def test_analyzer_returns_clusters():
    """Analyzer should parse LLM response into AnalysisCluster objects."""
    mock_response = MagicMock()
    mock_response.content = [MagicMock()]
    mock_response.content[0].text = json.dumps({
        "clusters": [
            {
                "theme": "Test pain point",
                "summary": "People are frustrated",
                "comment_count": 100,
                "video_count": 2,
                "potential": "HIGH",
                "app_idea": "Build an app",
                "sample_comments": ["comment 1", "comment 2"],
            }
        ]
    })

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    with patch("ideascroller.analyzer.AsyncAnthropic", return_value=mock_client):
        analyzer = Analyzer(api_key="test-key")
        result = await analyzer.analyze(
            session_id="test",
            videos=[Video(id="v1", session_id="test", author="u",
                          description="d", comment_count=100, url="url")],
            comments=[Comment(id="c1", video_id="v1", text="comment",
                              author="a", likes=0, reply_count=0)],
        )

    assert len(result.clusters) == 1
    assert result.clusters[0].theme == "Test pain point"
    assert result.clusters[0].potential == "HIGH"
    assert result.session_id == "test"


async def test_analyzer_empty_comments():
    """Analyzer should return empty clusters for no comments."""
    with patch("ideascroller.analyzer.AsyncAnthropic"):
        analyzer = Analyzer(api_key="test-key")
        result = await analyzer.analyze(session_id="test", videos=[], comments=[])

    assert len(result.clusters) == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_analyzer.py -v
```
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ideascroller/analyzer.py
"""LLM analysis of scraped TikTok comments using Anthropic Claude."""

import json
import logging

from anthropic import AsyncAnthropic

from ideascroller.models import AnalysisCluster, AnalysisResult, Comment, Video

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an expert product researcher analyzing TikTok comments to discover app and SaaS opportunities.

You will receive comments from TikTok videos grouped by video. Your job is to:

1. CLUSTER comments by recurring themes, frustrations, and pain points
2. RANK clusters by frequency (how many comments mention it) and intensity (how strongly people feel)
3. RATE each cluster's potential as an app/product idea: HIGH, MEDIUM, or LOW
4. SUGGEST a concrete app concept for each cluster

Respond with ONLY valid JSON matching this schema:
{
  "clusters": [
    {
      "theme": "Short theme title",
      "summary": "2-3 sentence explanation of the pain point",
      "comment_count": <approximate number of comments in this cluster>,
      "video_count": <number of videos where this theme appeared>,
      "potential": "HIGH" | "MEDIUM" | "LOW",
      "app_idea": "One-sentence app concept",
      "sample_comments": ["3-5 representative comments from the data"]
    }
  ]
}

Focus on pain points that could realistically be solved with software. Ignore off-topic comments, spam, and purely positive reactions. Rank HIGH potential clusters first."""


def build_analysis_prompt(videos: list[Video], comments: list[Comment]) -> str:
    """Build the user prompt with comments grouped by video."""
    if not comments:
        return "No comments were collected in this session. There is no data to analyze."

    comments_by_video: dict[str, list[Comment]] = {}
    for comment in comments:
        comments_by_video.setdefault(comment.video_id, []).append(comment)

    video_map = {v.id: v for v in videos}
    sections: list[str] = []

    for video_id, video_comments in comments_by_video.items():
        video = video_map.get(video_id)
        header = f"## Video: {video.description}" if video else f"## Video: {video_id}"
        if video:
            header += f"\nAuthor: @{video.author} | {video.comment_count} total comments"

        comment_lines = [
            f'- "{c.text}" ({c.likes} likes)' for c in video_comments
        ]
        sections.append(header + "\n" + "\n".join(comment_lines))

    return (
        f"Analyze the following {len(comments)} comments from {len(videos)} TikTok videos.\n"
        f"Identify pain points and app opportunities.\n\n"
        + "\n\n".join(sections)
    )


class Analyzer:
    """Runs LLM analysis on collected comments."""

    def __init__(self, api_key: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)

    async def analyze(
        self,
        session_id: str,
        videos: list[Video],
        comments: list[Comment],
    ) -> AnalysisResult:
        """Analyze comments and return clustered pain points."""
        if not comments:
            return AnalysisResult(
                session_id=session_id, clusters=[], raw_response="No comments to analyze"
            )

        prompt = build_analysis_prompt(videos, comments)
        logger.info("Sending %d comments to Claude for analysis", len(comments))

        response = await self._client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_text = response.content[0].text
        parsed = json.loads(raw_text)
        clusters = [AnalysisCluster(**c) for c in parsed["clusters"]]

        return AnalysisResult(
            session_id=session_id, clusters=clusters, raw_response=raw_text,
        )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_analyzer.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ideascroller/analyzer.py tests/test_analyzer.py
git commit -m "feat: add LLM analyzer with Claude for pain point clustering"
```

---

### Task 6: Playwright Scraper Engine

**Files:**
- Create: `src/ideascroller/scraper.py`
- Create: `tests/test_scraper_utils.py`

No full integration test — the scraper requires a live TikTok session. We test the pure utility function `parse_comment_count`.

- [ ] **Step 1: Write the failing test for parse_comment_count**

```python
# tests/test_scraper_utils.py
from ideascroller.scraper import parse_comment_count


def test_parse_plain_number():
    assert parse_comment_count("300") == 300


def test_parse_k_suffix():
    assert parse_comment_count("1.2K") == 1200


def test_parse_m_suffix():
    assert parse_comment_count("2.5M") == 2500000


def test_parse_empty():
    assert parse_comment_count("") == 0


def test_parse_invalid():
    assert parse_comment_count("abc") == 0


def test_parse_lowercase_k():
    assert parse_comment_count("1.2k") == 1200
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_scraper_utils.py -v
```
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the scraper module**

```python
# src/ideascroller/scraper.py
"""Playwright engine for scrolling TikTok FYP and scraping comments."""

import asyncio
import logging
import re
from typing import Callable, Optional

from playwright.async_api import Page, Response, async_playwright

from ideascroller.models import Comment, Video

logger = logging.getLogger(__name__)


def parse_comment_count(text: str) -> int:
    """Parse TikTok comment count text like '1.2K' or '300' into an integer."""
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
    """Scrolls TikTok FYP and scrapes comments from high-engagement videos."""

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
        """Signal the scraper to stop after the current video."""
        self._stop_event.set()

    def _log(self, message: str) -> None:
        logger.info(message)
        self._on_log(message)

    def _update_stats(self) -> None:
        self._on_stats_update(
            self._videos_scanned, self._videos_scraped, len(self._comments)
        )

    async def _handle_response(self, response: Response) -> None:
        """Intercept TikTok API responses for video metadata and comments."""
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

    async def run(self, session_id: str) -> None:
        """Main scroll loop — runs until stop() is called."""
        self._log("Launching browser...")
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                self._chrome_dir,
                headless=False,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            page.on("response", self._handle_response)

            self._log("Navigating to TikTok FYP...")
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
        """Check the currently visible video and scrape if it qualifies."""
        self._videos_scanned += 1

        # Try to get comment count from DOM
        try:
            count_el = page.locator('strong[data-e2e="comment-count"]').first
            count_text = await count_el.inner_text(timeout=3000)
            comment_count = parse_comment_count(count_text)
        except Exception:
            comment_count = 0

        # Try to get video ID from URL
        current_url = page.url
        video_id = self._extract_video_id(current_url)

        # Also check intercepted video items for more accurate count
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

        # Get video metadata
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

        video = Video(
            id=video_id,
            session_id=session_id,
            author=author,
            description=description[:500],
            comment_count=comment_count,
            url=current_url,
        )
        self._videos.append(video)
        self._videos_scraped += 1
        self._log(f"Scraping comments for video by @{author} ({comment_count} comments)...")

        await self._scrape_comments(page, video_id)
        self._update_stats()

    async def _scrape_comments(self, page: Page, video_id: str) -> None:
        """Open comment panel and collect all comments via XHR interception."""
        self._intercepted_comments.clear()

        # Click comment button to open panel
        try:
            comment_btn = page.locator('div[data-e2e="comment-button"]').first
            await comment_btn.click(timeout=5000)
            await asyncio.sleep(2)
        except Exception as e:
            self._log(f"Failed to open comment panel: {e}")
            return

        # Scroll comment panel to load all comments
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

            # Scroll the comment panel
            try:
                await page.evaluate("""() => {
                    const panel = document.querySelector('div[class*="DivCommentListContainer"]')
                        || document.querySelector('div[class*="DivCommentMain"]');
                    if (panel) panel.scrollTop = panel.scrollHeight;
                }""")
            except Exception:
                pass

            await asyncio.sleep(1.5)

        # Convert intercepted comments to our model
        for raw_comment in self._intercepted_comments:
            try:
                comment = Comment(
                    id=raw_comment.get("cid", ""),
                    video_id=video_id,
                    text=raw_comment.get("text", ""),
                    author=raw_comment.get("user", {}).get("unique_id", "unknown"),
                    likes=raw_comment.get("digg_count", 0),
                    reply_count=raw_comment.get("reply_comment_total", 0),
                )
                self._comments.append(comment)
            except Exception as e:
                logger.debug("Failed to parse comment: %s", e)

        self._log(f"Collected {len(self._intercepted_comments)} comments from video {video_id}")

        # Close comment panel
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(1)
        except Exception:
            pass

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        """Extract aweme_id from TikTok URL."""
        match = re.search(r"/video/(\d+)", url)
        return match.group(1) if match else None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_scraper_utils.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ideascroller/scraper.py tests/test_scraper_utils.py
git commit -m "feat: add Playwright scraper with XHR interception for TikTok FYP"
```

---

### Task 7: FastAPI Server

**Files:**
- Create: `src/ideascroller/server.py`
- Create: `tests/test_server.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server.py
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch, MagicMock
from ideascroller.server import create_app


@pytest.fixture
async def app(tmp_path):
    """Create test app with temporary database."""
    test_app = create_app(db_path=str(tmp_path / "test.db"))
    async with test_app.router.lifespan_context(test_app):
        yield test_app


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_index_returns_html(client: AsyncClient):
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_get_config(client: AsyncClient):
    response = await client.get("/config")
    assert response.status_code == 200
    data = response.json()
    assert "comment_threshold" in data


async def test_put_config(client: AsyncClient):
    response = await client.put("/config", json={"comment_threshold": 500})
    assert response.status_code == 200
    data = response.json()
    assert data["comment_threshold"] == 500


async def test_start_creates_session(client: AsyncClient):
    with patch("ideascroller.server.Scraper") as MockScraper:
        mock_instance = MagicMock()
        mock_instance.run = AsyncMock()
        mock_instance.videos = []
        mock_instance.comments = []
        mock_instance.videos_scanned = 0
        mock_instance.videos_scraped = 0
        MockScraper.return_value = mock_instance

        response = await client.post("/start")
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data


async def test_stop_without_session_returns_404(client: AsyncClient):
    response = await client.post("/stop")
    assert response.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_server.py -v
```
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write minimal implementation**

```python
# src/ideascroller/server.py
"""FastAPI server with WebSocket live log and REST API."""

import asyncio
import datetime
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ideascroller.analyzer import Analyzer
from ideascroller.config import Settings, get_chrome_user_data_dir
from ideascroller.db import Database
from ideascroller.models import LogEvent, Session, SessionStatus
from ideascroller.scraper import Scraper

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Manages WebSocket connections for live log streaming."""

    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections = [*self._connections, ws]

    def disconnect(self, ws: WebSocket) -> None:
        self._connections = [c for c in self._connections if c is not ws]

    async def broadcast(self, message: dict) -> None:
        dead: list[WebSocket] = []
        for ws in self._connections:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        if dead:
            self._connections = [c for c in self._connections if c not in dead]


class ConfigUpdate(BaseModel):
    comment_threshold: int


class AppState:
    def __init__(self) -> None:
        self.db: Optional[Database] = None
        self.settings: Settings = Settings()
        self.manager: ConnectionManager = ConnectionManager()
        self.current_session_id: Optional[str] = None
        self.scraper: Optional[Scraper] = None
        self.scrape_task: Optional[asyncio.Task] = None


_state = AppState()


def create_app(db_path: str = "ideascroller.db") -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _state.db = Database(db_path)
        await _state.db.initialize()
        _state.settings = Settings()
        yield
        if _state.scraper:
            _state.scraper.stop()
        if _state.scrape_task and not _state.scrape_task.done():
            _state.scrape_task.cancel()
        await _state.db.close()

    app = FastAPI(title="IdeaScroller", lifespan=lifespan)
    static_dir = Path(__file__).parent / "static"

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html_path = static_dir / "index.html"
        return HTMLResponse(content=html_path.read_text())

    @app.get("/config")
    async def get_config():
        return {"comment_threshold": _state.settings.comment_threshold}

    @app.put("/config")
    async def put_config(update: ConfigUpdate):
        _state.settings.comment_threshold = update.comment_threshold
        return {"comment_threshold": _state.settings.comment_threshold}

    @app.post("/start")
    async def start():
        if _state.scrape_task and not _state.scrape_task.done():
            return JSONResponse(status_code=409, content={"error": "Already running"})

        session = Session(threshold=_state.settings.comment_threshold)
        await _state.db.save_session(session)
        _state.current_session_id = session.id

        chrome_dir = get_chrome_user_data_dir(_state.settings)

        def sync_log(msg: str) -> None:
            loop = asyncio.get_event_loop()
            event = LogEvent(message=msg)
            loop.create_task(_state.manager.broadcast({
                "type": "log",
                "timestamp": event.timestamp.isoformat(),
                "message": event.message,
            }))

        def sync_stats(scanned: int, scraped: int, comments: int) -> None:
            loop = asyncio.get_event_loop()
            loop.create_task(_state.manager.broadcast({
                "type": "stats",
                "videos_scanned": scanned,
                "videos_scraped": scraped,
                "total_comments": comments,
            }))

        scraper = Scraper(
            chrome_user_data_dir=chrome_dir,
            comment_threshold=_state.settings.comment_threshold,
            on_log=sync_log,
            on_stats_update=sync_stats,
        )
        _state.scraper = scraper

        async def run_and_save():
            try:
                await scraper.run(session.id)
            except Exception as e:
                logger.error("Scraper error: %s", e)
            finally:
                for video in scraper.videos:
                    await _state.db.save_video(video)
                if scraper.comments:
                    await _state.db.save_comments(scraper.comments)
                updated_session = session.model_copy(update={
                    "videos_scanned": scraper.videos_scanned,
                    "videos_scraped": scraper.videos_scraped,
                    "total_comments": len(scraper.comments),
                })
                await _state.db.update_session(updated_session)

        _state.scrape_task = asyncio.create_task(run_and_save())
        return {"session_id": session.id}

    @app.post("/stop")
    async def stop():
        if not _state.current_session_id or not _state.scraper:
            return JSONResponse(status_code=404, content={"error": "No active session"})

        session_id = _state.current_session_id
        _state.scraper.stop()

        if _state.scrape_task:
            try:
                await asyncio.wait_for(_state.scrape_task, timeout=30)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        session = await _state.db.get_session(session_id)
        if session:
            updated = session.model_copy(update={
                "status": SessionStatus.ANALYZING,
                "stopped_at": datetime.datetime.now(),
            })
            await _state.db.update_session(updated)

        await _state.manager.broadcast({
            "type": "log",
            "timestamp": datetime.datetime.now().isoformat(),
            "message": "Analyzing comments...",
        })

        try:
            analyzer = Analyzer(api_key=_state.settings.anthropic_api_key)
            videos = await _state.db.get_videos(session_id)
            comments = await _state.db.get_session_comments(session_id)
            result = await analyzer.analyze(session_id, videos, comments)
            await _state.db.save_analysis(result)

            session = await _state.db.get_session(session_id)
            if session:
                updated = session.model_copy(update={"status": SessionStatus.COMPLETE})
                await _state.db.update_session(updated)

            await _state.manager.broadcast({
                "type": "analysis",
                "clusters": [c.model_dump() for c in result.clusters],
            })
            await _state.manager.broadcast({
                "type": "log",
                "timestamp": datetime.datetime.now().isoformat(),
                "message": "Analysis complete!",
            })

        except Exception as e:
            logger.error("Analysis error: %s", e)
            session = await _state.db.get_session(session_id)
            if session:
                updated = session.model_copy(update={"status": SessionStatus.ERROR})
                await _state.db.update_session(updated)
            await _state.manager.broadcast({
                "type": "log",
                "timestamp": datetime.datetime.now().isoformat(),
                "message": f"Analysis failed: {e}",
            })

        _state.current_session_id = None
        _state.scraper = None
        _state.scrape_task = None
        return {"status": "stopped", "session_id": session_id}

    @app.get("/results/{session_id}")
    async def get_results(session_id: str):
        result = await _state.db.get_analysis(session_id)
        if not result:
            return JSONResponse(status_code=404, content={"error": "No analysis found"})
        return {"clusters": [c.model_dump() for c in result.clusters]}

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        await _state.manager.connect(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            _state.manager.disconnect(ws)

    return app
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_server.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/ideascroller/server.py tests/test_server.py
git commit -m "feat: add FastAPI server with start/stop/config/websocket endpoints"
```

---

### Task 8: Frontend GUI

**Files:**
- Create: `src/ideascroller/static/index.html`

- [ ] **Step 1: Create the static directory**

```bash
mkdir -p "/Users/zaheemnazoordeen/tiktok scroller/src/ideascroller/static"
```

- [ ] **Step 2: Create index.html**

Write a single-page HTML file with:
- Header with title "IdeaScroller" and configurable threshold input
- Start (green) and Stop (red) buttons
- Live counters: videos scanned, scraped, total comments
- Monospace log box (200px height, auto-scroll)
- Analysis results section (hidden until analysis completes)
- WebSocket connection for real-time updates
- All user-provided text rendered via `textContent` (never raw `innerHTML`) to prevent XSS
- Dark theme matching the mockup: `#0f0f1a` background, `#2a2a3e` borders
- CSS classes: `.cluster` cards with colored left borders (gold=HIGH, purple=MEDIUM, gray=LOW)
- JavaScript functions: `connectWebSocket()`, `startScrolling()`, `stopScrolling()`, `renderClusters()`

The HTML file should use DOM API methods (`createElement`, `textContent`, `appendChild`) instead of `innerHTML` for all dynamic content to prevent XSS.

- [ ] **Step 3: Commit**

```bash
git add src/ideascroller/static/index.html
git commit -m "feat: add single-page GUI with live log and analysis display"
```

---

### Task 9: Entry Point

**Files:**
- Create: `src/ideascroller/__main__.py`

- [ ] **Step 1: Create the entry point**

```python
# src/ideascroller/__main__.py
"""Entry point for running IdeaScroller."""

import logging

import uvicorn

from ideascroller.config import Settings
from ideascroller.server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def main() -> None:
    settings = Settings()
    app = create_app()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Reinstall package**

```bash
pip install -e ".[dev]"
```

- [ ] **Step 3: Commit**

```bash
git add src/ideascroller/__main__.py
git commit -m "feat: add CLI entry point"
```

---

### Task 10: Integration Test

**Files:**
- Create: `tests/test_integration.py`

- [ ] **Step 1: Write integration test**

```python
# tests/test_integration.py
"""Integration test: server creates session, stores data, runs analysis."""

import asyncio
import json
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, MagicMock, patch

from ideascroller.server import create_app
from ideascroller.models import Comment, Video


@pytest.fixture
async def integration_app(tmp_path):
    db_path = str(tmp_path / "integration.db")
    app = create_app(db_path=db_path)
    async with app.router.lifespan_context(app):
        yield app, db_path


@pytest.fixture
async def integration_client(integration_app):
    app, _ = integration_app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_full_flow_with_mock_scraper_and_analyzer(integration_app, integration_client):
    """Test the complete flow: start -> scraper runs -> stop -> analysis returns clusters."""
    app, db_path = integration_app
    client = integration_client

    mock_videos = [
        Video(id="v1", session_id="will-be-replaced", author="creator1",
              description="Why meal planning sucks", comment_count=500,
              url="https://tiktok.com/@creator1/video/v1"),
    ]
    mock_comments = [
        Comment(id="c1", video_id="v1", text="I hate meal planning so much",
                author="user1", likes=50, reply_count=2),
        Comment(id="c2", video_id="v1", text="Same, it's so overwhelming",
                author="user2", likes=30, reply_count=0),
        Comment(id="c3", video_id="v1", text="Someone build an app for this",
                author="user3", likes=100, reply_count=5),
    ]

    mock_analysis_response = MagicMock()
    mock_analysis_response.content = [MagicMock()]
    mock_analysis_response.content[0].text = json.dumps({
        "clusters": [{
            "theme": "Meal planning overwhelm",
            "summary": "People find meal planning stressful",
            "comment_count": 3,
            "video_count": 1,
            "potential": "HIGH",
            "app_idea": "Fridge-scan meal suggester",
            "sample_comments": ["I hate meal planning so much"],
        }]
    })

    with patch("ideascroller.server.Scraper") as MockScraper, \
         patch("ideascroller.analyzer.AsyncAnthropic") as MockAnthropic:

        mock_scraper = MagicMock()

        async def mock_run(session_id):
            for v in mock_videos:
                mock_scraper.videos.append(
                    v.model_copy(update={"session_id": session_id})
                )
            mock_scraper.comments.extend(mock_comments)

        mock_scraper.run = AsyncMock(side_effect=mock_run)
        mock_scraper.stop = MagicMock()
        mock_scraper.videos = []
        mock_scraper.comments = []
        mock_scraper.videos_scanned = 10
        mock_scraper.videos_scraped = 1
        MockScraper.return_value = mock_scraper

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_analysis_response)
        MockAnthropic.return_value = mock_client

        # Start
        start_resp = await client.post("/start")
        assert start_resp.status_code == 200
        session_id = start_resp.json()["session_id"]

        await asyncio.sleep(0.5)

        # Stop (triggers analysis)
        stop_resp = await client.post("/stop")
        assert stop_resp.status_code == 200

        # Get results
        results_resp = await client.get(f"/results/{session_id}")
        assert results_resp.status_code == 200
        clusters = results_resp.json()["clusters"]
        assert len(clusters) == 1
        assert clusters[0]["theme"] == "Meal planning overwhelm"
        assert clusters[0]["potential"] == "HIGH"
```

- [ ] **Step 2: Run integration test**

```bash
pytest tests/test_integration.py -v
```
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: add integration test for full scrape-to-analysis flow"
```

---

### Task 11: Run All Tests and Verify

- [ ] **Step 1: Run full test suite**

```bash
pytest tests/ -v --tb=short
```
Expected: All tests PASS

- [ ] **Step 2: Check test coverage**

```bash
pytest tests/ --cov=ideascroller --cov-report=term-missing
```
Expected: 80%+ coverage on config, models, db, analyzer modules

- [ ] **Step 3: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "chore: verify all tests pass with coverage"
```
