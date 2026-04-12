# IdeaScroller — TikTok Pain Point Discovery Tool

## Overview

IdeaScroller scrolls through a user's TikTok For You page, identifies videos with high comment counts (configurable threshold, default 300), scrapes all comments from those videos, and uses an LLM to cluster the comments into pain point themes with app idea suggestions.

## Goals

1. Automatically scroll through TikTok FYP in a visible browser window using the user's logged-in Chrome profile
2. Detect videos exceeding the comment threshold via XHR interception (no DOM parsing for counts)
3. Scrape all comments from qualifying videos via TikTok's internal comment API
4. After the user stops the session, analyze all collected comments with Claude to identify pain points and app opportunities
5. Display results in a minimal web GUI

## Non-Goals

- Mobile app or browser extension
- TikTok account management or posting
- Real-time analysis during scrolling (analysis happens after stop)
- Multi-user support

## Architecture

### Components

**1. FastAPI Server (`:8000`)**
- `POST /start` — launches Playwright scroll loop in a background task
- `POST /stop` — signals the scroll loop to stop, triggers LLM analysis
- `WS /ws` — streams live log events to the GUI
- `GET /results/{session_id}` — returns analysis results
- `GET /config` — returns current settings
- `PUT /config` — updates settings (comment threshold)

**2. Playwright Engine**
- Connects to the user's existing Chrome profile via `launch_persistent_context` using the user's Chrome user data directory
- Navigates to `https://www.tiktok.com/foryou`
- Sets up XHR interception on two endpoints:
  - `/api/post/item_list/` — video metadata with `stats.commentCount` as integer
  - `/api/comment/list/` — paginated comment data
- Scroll loop: advance through FYP videos one at a time using `page.keyboard.press("ArrowDown")`
- For qualifying videos (comment count >= threshold):
  - Extract `aweme_id` from the page URL or intercepted video metadata
  - Click the comment button (`div[data-e2e="comment-button"]`) to open the panel
  - Scroll the comment panel to trigger cursor-paginated `/api/comment/list/` requests
  - Collect all pages until `has_more` is 0 or all comments are fetched
  - Close the comment panel and continue scrolling
- Runs in headed mode (visible browser window)

**3. Web GUI**
- Single HTML page served by FastAPI at `/`
- Plain HTML/CSS/JS, no framework
- Elements:
  - Header: "IdeaScroller" title + configurable threshold display
  - Controls: Start (green) / Stop (red) buttons
  - Live counters: videos scanned, videos scraped, total comments collected
  - Live log: monospace scrollable area showing timestamped events via WebSocket
  - Analysis results panel: appears after stop, shows clustered pain points

**4. LLM Analyzer**
- Uses Anthropic Python SDK with Claude Sonnet (claude-sonnet-4-6)
- Input: all comments from the session, grouped by video (includes video description for context)
- Prompt instructs Claude to:
  1. Cluster comments by recurring themes/frustrations/pain points
  2. Rank clusters by frequency (how many comments) and intensity (how strongly people feel)
  3. Rate each cluster's potential as an app/product idea: HIGH, MEDIUM, or LOW
  4. For each cluster, suggest a concrete app concept
- Output: structured JSON matching the `AnalysisCluster` schema
- If total comment text exceeds ~150K tokens (roughly 10K+ comments), split into batches of videos, analyze each batch independently, then make a final merge call that combines the per-batch clusters into a unified ranking
- API key sourced from `ANTHROPIC_API_KEY` environment variable

### Data Model (SQLite)

**sessions**
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| started_at | DATETIME | When scrolling started |
| stopped_at | DATETIME | When user hit stop |
| status | TEXT | running, analyzing, complete, error |
| threshold | INTEGER | Comment count threshold used |
| videos_scanned | INTEGER | Total videos seen |
| videos_scraped | INTEGER | Videos that met threshold |
| total_comments | INTEGER | Total comments collected |

**videos**
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (aweme_id) | TikTok video ID |
| session_id | TEXT | FK to sessions |
| author | TEXT | Video creator username |
| description | TEXT | Video caption/description |
| comment_count | INTEGER | Total comment count |
| url | TEXT | Full TikTok URL |

**comments**
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (cid) | TikTok comment ID |
| video_id | TEXT | FK to videos |
| text | TEXT | Comment content |
| author | TEXT | Commenter username |
| likes | INTEGER | Comment like count |
| reply_count | INTEGER | Number of replies |
| created_at | DATETIME | When comment was posted |

**analyses**
| Column | Type | Description |
|--------|------|-------------|
| id | TEXT (UUID) | Primary key |
| session_id | TEXT | FK to sessions |
| clusters | JSON | Array of AnalysisCluster objects |
| raw_response | TEXT | Full LLM response for debugging |
| created_at | DATETIME | When analysis completed |

### Analysis Output Schema

```json
{
  "clusters": [
    {
      "theme": "Meal planning is overwhelming",
      "summary": "People are frustrated with...",
      "comment_count": 342,
      "video_count": 5,
      "potential": "HIGH",
      "app_idea": "Simple 'what's in your fridge' → meal suggestion app",
      "sample_comments": ["comment1", "comment2", "comment3"]
    }
  ]
}
```

## TikTok DOM Selectors

Stable `data-e2e` attributes (preferred over CSS classes which change on deploys):

| Element | Selector |
|---------|----------|
| FYP video container | `div[data-e2e="recommend-list-item-container"]` |
| Comment count | `strong[data-e2e="comment-count"]` |
| Comment button | `div[data-e2e="comment-button"]` |
| Video author | `h3[data-e2e="video-author-uniqueid"]` |
| Video description | `div[data-e2e="video-desc"]` |
| Like count | `strong[data-e2e="like-count"]` |

## TikTok Internal APIs

**Video feed:** `GET /api/post/item_list/`
- Intercepted automatically as user scrolls FYP
- Response includes `itemList[].stats.commentCount` as integer
- Also provides `itemList[].id` (aweme_id)

**Comments:** `GET /api/comment/list/`
- Parameters: `aweme_id`, `count` (default 20), `cursor` (pagination offset)
- Response: `{ comments: [...], total: int, has_more: 0|1, cursor: int }`
- Each comment: `{ cid, text, digg_count, reply_comment_total, create_time, user: { unique_id, nickname } }`
- Requests must come from the browser (signed with `msToken`, `X-Bogus`) — cannot be replayed externally

## Tech Stack

- **Python 3.12+**
- **FastAPI** — web server, WebSocket support
- **Playwright** (Python) — browser automation
- **Anthropic Python SDK** — LLM analysis
- **SQLite** (via `aiosqlite`) — async data storage
- **uvicorn** — ASGI server
- **Plain HTML/CSS/JS** — frontend (no build step)

## File Structure

```
tiktok-scroller/
├── pyproject.toml
├── .env.example          # ANTHROPIC_API_KEY, CHROME_USER_DATA_DIR
├── src/
│   └── ideascroller/
│       ├── __init__.py
│       ├── config.py      # Settings, env vars, defaults
│       ├── models.py      # Pydantic models for API and analysis
│       ├── db.py          # SQLite setup and queries
│       ├── scraper.py     # Playwright engine — scroll, intercept, scrape
│       ├── analyzer.py    # LLM analysis with Anthropic SDK
│       ├── server.py      # FastAPI app, routes, WebSocket
│       └── static/
│           └── index.html # Single-page GUI
├── tests/
│   ├── test_scraper.py
│   ├── test_analyzer.py
│   ├── test_db.py
│   └── test_server.py
└── docs/
```

## Configuration

Environment variables (`.env` file):
- `ANTHROPIC_API_KEY` — required for LLM analysis
- `CHROME_USER_DATA_DIR` — path to Chrome profile directory (default: platform-specific Chrome default)
- `COMMENT_THRESHOLD` — default comment count threshold (default: 300)
- `HOST` — server host (default: `127.0.0.1`)
- `PORT` — server port (default: `8000`)

## Error Handling

- **TikTok login prompt**: If detected during scrolling, log a warning and pause. The user can log in manually in the visible browser.
- **XHR interception fails**: Log the failure, skip that video, continue scrolling.
- **Comment pagination stalls**: Timeout after 10 seconds of no new data, move to next video.
- **LLM API error**: Retry once, then save error state to session. User can re-trigger analysis.
- **Browser crash**: Catch the exception, update session status to "error", log details.

## Security

- API key read from environment variable only, never hardcoded
- SQLite database stored locally, no network exposure
- FastAPI server binds to `127.0.0.1` only (localhost)
- No authentication needed (local tool, single user)
