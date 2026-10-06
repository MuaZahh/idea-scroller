"""Autonomous IdeaScroller operator.

Drives POST /start, tails the WebSocket live log/stats, decides when to stop
based on video count / elapsed time, then watches the analysis+validator phase
stream in. Prints every significant event so the session is observable.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import websockets

SERVER = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws"


@dataclass
class RunState:
    session_id: str | None = None
    videos_scraped: int = 0
    total_comments: int = 0
    started_at: float = field(default_factory=time.time)
    clusters: list[dict] = field(default_factory=list)
    stopped: bool = False
    analysis_done: bool = False
    recent_logs: list[str] = field(default_factory=list)


# Stop rules — honest thresholds so we get real signal
MIN_VIDEOS_SCRAPED = 8      # need at least this many before stopping
MAX_VIDEOS_SCRAPED = 20     # stop once we have this many
MAX_DURATION_SEC = 420       # 7 minutes hard cap (in case scraping stalls)
STALL_WINDOW_SEC = 90        # if no new video in 90s, stop


def ts() -> str:
    return time.strftime("%H:%M:%S")


def log(tag: str, msg: str) -> None:
    print(f"[{ts()}] {tag:<14} {msg}", flush=True)


async def start_session(niche: str, settings: dict) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        # Push config first
        cfg_body = {**settings, "niche": niche}
        r = await client.put(f"{SERVER}/config", json=cfg_body)
        r.raise_for_status()
        log("CONFIG", f"niche={niche!r}  {settings}")

        r = await client.post(f"{SERVER}/start")
        r.raise_for_status()
        sid = r.json()["session_id"]
        log("START", f"session_id={sid}")
        return sid


async def stop_session() -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f"{SERVER}/stop")
        r.raise_for_status()
        log("STOP", f"{r.json()}")


async def print_final_cards(state: RunState) -> None:
    if not state.clusters:
        log("RESULT", "No clusters surfaced — analysis returned empty")
        return
    print("\n" + "=" * 78)
    print(f"  FINAL IDEA CARDS — {len(state.clusters)} cluster(s)")
    print("=" * 78)
    for i, c in enumerate(state.clusters, 1):
        v = c.get("validation") or {}
        tm = v.get("trustmrr") or {}
        rd = v.get("reddit") or {}
        kw = v.get("keyword") or {}
        vi = v.get("virality") or {}
        print(f"\n#{i}  {c.get('theme', '?')}")
        print(f"    idea:   {c.get('app_idea', '')[:95]}")
        print(f"    market: {c.get('market')}  edge: {c.get('edge', '')[:70]}")
        print(f"    comments={c.get('comment_count')}  videos={c.get('video_count')}")
        if tm:
            comps = tm.get("top_competitors") or []
            top_line = ", ".join(f"{cc['name']} ${cc.get('mrr_usd',0):,.0f}" for cc in comps[:3])
            print(f"    📊 TrustMRR {tm.get('verdict')}  max=${tm.get('max_mrr_usd',0):,.0f}/mo  [{top_line}]")
        if rd:
            print(f"    💬 Reddit   {rd.get('verdict')}  {rd.get('total_threads_found')} threads  "
                  f"{rd.get('total_upvotes')} upvotes  subs={rd.get('subreddits', [])[:4]}")
        if kw:
            wiki = kw.get("wikipedia_monthly_views")
            wiki_s = f"{wiki:,}/mo" if wiki else "—"
            print(f"    📈 Keyword  {kw.get('verdict')}  suggestions={kw.get('suggestion_count')}  "
                  f"wiki={wiki_s}  source={kw.get('source')}")
        if vi:
            print(f"    🔥 Virality videos={vi.get('videos')}  "
                  f"total={vi.get('total_views'):,}  max={vi.get('max_views'):,}")


def should_stop(state: RunState) -> str | None:
    """Return a human-readable reason to stop, or None to keep running."""
    elapsed = time.time() - state.started_at
    if elapsed > MAX_DURATION_SEC:
        return f"hit max duration ({MAX_DURATION_SEC}s)"
    if state.videos_scraped >= MAX_VIDEOS_SCRAPED:
        return f"reached {MAX_VIDEOS_SCRAPED} scraped videos"
    # stall detection: look at time since last "scraped" log line
    if state.videos_scraped >= MIN_VIDEOS_SCRAPED:
        # look for recent scrape activity
        last_activity = getattr(should_stop, "_last_activity", state.started_at)
        if (time.time() - last_activity) > STALL_WINDOW_SEC:
            return f"no new video in {STALL_WINDOW_SEC}s (have {state.videos_scraped})"
    return None


async def monitor_stream(state: RunState) -> None:
    """Consume /ws events, update state, decide when to stop."""
    async with websockets.connect(WS_URL, ping_interval=20) as ws:
        log("WS", "connected")
        last_comment_scraped_count = -1
        while not state.analysis_done:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            except asyncio.TimeoutError:
                # Periodic stop-check even during quiet periods
                reason = should_stop(state)
                if reason and not state.stopped:
                    state.stopped = True
                    log("DECISION", f"stopping — {reason}")
                    asyncio.create_task(stop_session())
                continue

            try:
                msg = json.loads(raw)
            except Exception:
                continue

            t = msg.get("type")
            if t == "log":
                line = msg.get("message", "")
                state.recent_logs.append(line)
                state.recent_logs = state.recent_logs[-40:]
                # Surface the interesting log lines
                lower = line.lower()
                if "comments collected" in lower or "scraped" in lower or "captcha" in lower:
                    log("SCRAPER", line[:110])
                    should_stop._last_activity = time.time()  # type: ignore[attr-defined]
                elif "analyzing" in lower or "batch" in lower or "merging" in lower or "validator" in lower:
                    log("ANALYZER", line[:110])
                elif "no comments" in lower or "error" in lower or "failed" in lower:
                    log("ERROR", line[:110])
            elif t == "stats":
                st = msg.get("stats") or msg
                new_videos = st.get("videos_scraped", state.videos_scraped)
                new_comments = st.get("total_comments", state.total_comments)
                if new_videos > state.videos_scraped or new_comments > state.total_comments:
                    log("STATS", f"scanned={st.get('videos_scanned','?')}  scraped={new_videos}  comments={new_comments}")
                    state.videos_scraped = new_videos
                    state.total_comments = new_comments
                    should_stop._last_activity = time.time()  # type: ignore[attr-defined]
            elif t == "analysis":
                state.clusters = msg.get("clusters", [])
                state.analysis_done = True
                log("ANALYSIS", f"{len(state.clusters)} cluster(s) received")

            # Evaluate stop condition
            if not state.stopped:
                reason = should_stop(state)
                if reason:
                    state.stopped = True
                    log("DECISION", f"stopping — {reason}")
                    asyncio.create_task(stop_session())


async def main() -> int:
    niche = sys.argv[1] if len(sys.argv) > 1 else "productivity"
    state = RunState()

    settings = {
        "comment_threshold": 100,      # lower than default so we pick up more videos
        "max_comments_per_video": 40,
        "max_videos": MAX_VIDEOS_SCRAPED,
        "analysis_mode": "balanced",
    }

    try:
        state.session_id = await start_session(niche, settings)
    except Exception as exc:
        log("FATAL", f"start failed: {exc}")
        return 1

    try:
        await monitor_stream(state)
    except Exception as exc:
        log("FATAL", f"stream error: {exc}")

    # If analysis hasn't arrived, give it a minute after stop
    if not state.analysis_done:
        log("WAIT", "analysis not yet received, waiting up to 120s...")
        try:
            await asyncio.wait_for(monitor_stream(state), timeout=120)
        except asyncio.TimeoutError:
            log("WAIT", "analysis never arrived")

    await print_final_cards(state)
    return 0 if state.clusters else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
