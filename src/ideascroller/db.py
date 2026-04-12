"""SQLite database layer for session, video, comment, and analysis storage."""

import datetime
import json
from typing import Optional

import aiosqlite

from ideascroller.models import (
    AnalysisCluster, AnalysisResult, Comment, Session, SessionStatus, Video,
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
        cursor = await self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        rows = await cursor.fetchall()
        return [row["name"] for row in rows]

    async def save_session(self, session: Session) -> None:
        await self._conn.execute(
            """INSERT INTO sessions (id, started_at, stopped_at, status, threshold,
               videos_scanned, videos_scraped, total_comments) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session.id, session.started_at.isoformat(),
             session.stopped_at.isoformat() if session.stopped_at else None,
             session.status.value, session.threshold, session.videos_scanned,
             session.videos_scraped, session.total_comments))
        await self._conn.commit()

    async def get_session(self, session_id: str) -> Optional[Session]:
        cursor = await self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return Session(
            id=row["id"],
            started_at=datetime.datetime.fromisoformat(row["started_at"]),
            stopped_at=datetime.datetime.fromisoformat(row["stopped_at"]) if row["stopped_at"] else None,
            status=SessionStatus(row["status"]), threshold=row["threshold"],
            videos_scanned=row["videos_scanned"], videos_scraped=row["videos_scraped"],
            total_comments=row["total_comments"])

    async def update_session(self, session: Session) -> None:
        await self._conn.execute(
            """UPDATE sessions SET stopped_at=?, status=?, videos_scanned=?,
               videos_scraped=?, total_comments=? WHERE id=?""",
            (session.stopped_at.isoformat() if session.stopped_at else None,
             session.status.value, session.videos_scanned, session.videos_scraped,
             session.total_comments, session.id))
        await self._conn.commit()

    async def save_video(self, video: Video) -> None:
        await self._conn.execute(
            """INSERT OR IGNORE INTO videos (id, session_id, author, description, comment_count, url)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (video.id, video.session_id, video.author, video.description, video.comment_count, video.url))
        await self._conn.commit()

    async def get_videos(self, session_id: str) -> list[Video]:
        cursor = await self._conn.execute("SELECT * FROM videos WHERE session_id = ?", (session_id,))
        rows = await cursor.fetchall()
        return [Video(id=row["id"], session_id=row["session_id"], author=row["author"],
                      description=row["description"], comment_count=row["comment_count"], url=row["url"])
                for row in rows]

    async def save_comments(self, comments: list[Comment]) -> None:
        await self._conn.executemany(
            """INSERT OR IGNORE INTO comments (id, video_id, text, author, likes, reply_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(c.id, c.video_id, c.text, c.author, c.likes, c.reply_count,
              c.created_at.isoformat() if c.created_at else None) for c in comments])
        await self._conn.commit()

    async def get_comments(self, video_id: str) -> list[Comment]:
        cursor = await self._conn.execute("SELECT * FROM comments WHERE video_id = ?", (video_id,))
        rows = await cursor.fetchall()
        return [Comment(id=row["id"], video_id=row["video_id"], text=row["text"],
                        author=row["author"], likes=row["likes"], reply_count=row["reply_count"],
                        created_at=datetime.datetime.fromisoformat(row["created_at"]) if row["created_at"] else None)
                for row in rows]

    async def get_session_comments(self, session_id: str) -> list[Comment]:
        cursor = await self._conn.execute(
            """SELECT c.* FROM comments c JOIN videos v ON c.video_id = v.id WHERE v.session_id = ?""",
            (session_id,))
        rows = await cursor.fetchall()
        return [Comment(id=row["id"], video_id=row["video_id"], text=row["text"],
                        author=row["author"], likes=row["likes"], reply_count=row["reply_count"],
                        created_at=datetime.datetime.fromisoformat(row["created_at"]) if row["created_at"] else None)
                for row in rows]

    async def save_analysis(self, result: AnalysisResult) -> None:
        clusters_json = json.dumps([c.model_dump() for c in result.clusters])
        await self._conn.execute(
            """INSERT INTO analyses (id, session_id, clusters, raw_response, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (result.id, result.session_id, clusters_json, result.raw_response, result.created_at.isoformat()))
        await self._conn.commit()

    async def get_analysis(self, session_id: str) -> Optional[AnalysisResult]:
        cursor = await self._conn.execute(
            "SELECT * FROM analyses WHERE session_id = ? ORDER BY created_at DESC LIMIT 1", (session_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        clusters = [AnalysisCluster(**c) for c in json.loads(row["clusters"])]
        return AnalysisResult(id=row["id"], session_id=row["session_id"], clusters=clusters,
                              raw_response=row["raw_response"],
                              created_at=datetime.datetime.fromisoformat(row["created_at"]))
