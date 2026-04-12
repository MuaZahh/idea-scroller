import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch, MagicMock
from ideascroller.server import create_app


@pytest.fixture
async def app(tmp_path):
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
