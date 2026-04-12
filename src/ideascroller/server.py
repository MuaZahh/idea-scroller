"""FastAPI server with WebSocket live log and REST API."""

import asyncio
import datetime
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from ideascroller.analyzer import analyze_comments
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


def _get_api_key() -> str:
    """Read API key fresh from .env file every time."""
    # Try multiple locations for .env
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).parent.parent.parent / ".env",  # src/ideascroller/../../.env
    ]
    for env_path in candidates:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                if line.startswith("ANTHROPIC_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'").strip()
                    if key:
                        logger.info("API key loaded from %s: %s...%s", env_path, key[:10], key[-5:])
                        return key
    # Fallback to env var
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        logger.info("API key from env var: %s...%s", key[:10], key[-5:])
    else:
        logger.error("No ANTHROPIC_API_KEY found in .env or environment")
    return key


def create_app(db_path: str = "ideascroller.db") -> FastAPI:

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _state.db = Database(db_path)
        await _state.db.initialize()
        _state.settings = Settings()
        _state.current_session_id = None
        _state.scraper = None
        _state.scrape_task = None
        _state.manager = ConnectionManager()
        yield
        # Graceful shutdown — stop scraper, save data, run analysis
        logger.info("Shutting down — saving session data...")
        if _state.scraper:
            _state.scraper.stop()
        if _state.scrape_task and not _state.scrape_task.done():
            try:
                await asyncio.wait_for(_state.scrape_task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        if _state.scraper and _state.current_session_id:
            session_id = _state.current_session_id
            try:
                # Save scraped data
                for video in _state.scraper.videos:
                    await _state.db.save_video(video)
                if _state.scraper.comments:
                    await _state.db.save_comments(_state.scraper.comments)

                logger.info(
                    "Saved %d videos, %d comments",
                    len(_state.scraper.videos),
                    len(_state.scraper.comments),
                )

                # Run Claude analysis if we have comments
                api_key = _get_api_key()
                if _state.scraper.comments and api_key:
                    logger.info("Running Claude analysis before shutdown...")
                    session = await _state.db.get_session(session_id)
                    if session:
                        updated = session.model_copy(update={
                            "status": SessionStatus.ANALYZING,
                            "stopped_at": datetime.datetime.now(),
                            "videos_scanned": _state.scraper.videos_scanned,
                            "videos_scraped": _state.scraper.videos_scraped,
                            "total_comments": len(_state.scraper.comments),
                        })
                        await _state.db.update_session(updated)

                    try:
                        videos = await _state.db.get_videos(session_id)
                        comments = await _state.db.get_session_comments(session_id)

                        from ideascroller.analyzer import analyze_comments
                        result = await analyze_comments(
                            api_key=api_key,
                            session_id=session_id,
                            videos=videos,
                            comments=comments,
                        )
                        await _state.db.save_analysis(result)

                        logger.info("Analysis complete! %d clusters found", len(result.clusters))
                        for cluster in result.clusters:
                            logger.info(
                                "  [%s] %s — %s",
                                cluster.potential,
                                cluster.theme,
                                cluster.app_idea,
                            )

                        session = await _state.db.get_session(session_id)
                        if session:
                            updated = session.model_copy(update={"status": SessionStatus.COMPLETE})
                            await _state.db.update_session(updated)

                    except Exception as e:
                        logger.error("Analysis failed: %s", e)
                        session = await _state.db.get_session(session_id)
                        if session:
                            updated = session.model_copy(update={"status": SessionStatus.ERROR})
                            await _state.db.update_session(updated)
                else:
                    if not api_key:
                        logger.warning("No ANTHROPIC_API_KEY set — skipping analysis")
                    session = await _state.db.get_session(session_id)
                    if session:
                        updated = session.model_copy(update={
                            "status": SessionStatus.COMPLETE,
                            "stopped_at": datetime.datetime.now(),
                            "videos_scanned": _state.scraper.videos_scanned,
                            "videos_scraped": _state.scraper.videos_scraped,
                            "total_comments": len(_state.scraper.comments),
                        })
                        await _state.db.update_session(updated)

            except Exception as e:
                logger.error("Failed to save data on shutdown: %s", e)

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
            api_key = _get_api_key()
            videos = await _state.db.get_videos(session_id)
            comments = await _state.db.get_session_comments(session_id)

            from ideascroller.analyzer import analyze_comments

            async def broadcast_log(msg: str) -> None:
                logger.info(msg)
                await _state.manager.broadcast({
                    "type": "log",
                    "timestamp": datetime.datetime.now().isoformat(),
                    "message": msg,
                })

            result = await analyze_comments(
                api_key=api_key,
                session_id=session_id,
                videos=videos,
                comments=comments,
                on_log=broadcast_log,
            )
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
