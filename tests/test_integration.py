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
         patch("anthropic.AsyncAnthropic") as MockAnthropic, \
         patch("ideascroller.server._get_api_keys", return_value={"ANTHROPIC_API_KEY": "test-key"}):

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

        start_resp = await client.post("/start")
        assert start_resp.status_code == 200
        session_id = start_resp.json()["session_id"]

        await asyncio.sleep(0.5)

        stop_resp = await client.post("/stop")
        assert stop_resp.status_code == 200

        results_resp = await client.get(f"/results/{session_id}")
        assert results_resp.status_code == 200
        clusters = results_resp.json()["clusters"]
        assert len(clusters) == 1
        assert clusters[0]["theme"] == "Meal planning overwhelm"
        assert clusters[0]["potential"] == "HIGH"
