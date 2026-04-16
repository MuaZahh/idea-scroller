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
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    started_at: datetime.datetime = Field(default_factory=datetime.datetime.now)
    stopped_at: Optional[datetime.datetime] = None
    status: SessionStatus = SessionStatus.RUNNING
    threshold: int = 300
    videos_scanned: int = 0
    videos_scraped: int = 0
    total_comments: int = 0


class Video(BaseModel):
    id: str
    session_id: str
    author: str
    description: str
    comment_count: int
    url: str


class Comment(BaseModel):
    id: str
    video_id: str
    text: str
    author: str
    likes: int = 0
    reply_count: int = 0
    created_at: Optional[datetime.datetime] = None


class AnalysisCluster(BaseModel):
    theme: str
    summary: str
    comment_count: int
    video_count: int
    potential: str
    app_idea: str
    competitors: list[str] = []
    market: str = "OPEN"
    edge: str = ""
    sample_comments: list[str] = []


class AnalysisResult(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str
    clusters: list[AnalysisCluster]
    raw_response: str = ""
    created_at: datetime.datetime = Field(default_factory=datetime.datetime.now)


class LogEvent(BaseModel):
    timestamp: datetime.datetime = Field(default_factory=datetime.datetime.now)
    message: str


class SessionStats(BaseModel):
    videos_scanned: int = 0
    videos_scraped: int = 0
    total_comments: int = 0
