import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from ideascroller.analyzer import Analyzer, build_analysis_prompt
from ideascroller.models import Comment, Video


def test_build_analysis_prompt_groups_by_video():
    videos = [Video(id="v1", session_id="s1", author="user1", description="Cooking tips",
                    comment_count=350, url="https://tiktok.com/v1")]
    comments = [
        Comment(id="c1", video_id="v1", text="I hate meal planning", author="a", likes=50, reply_count=0),
        Comment(id="c2", video_id="v1", text="Same, so overwhelming", author="b", likes=30, reply_count=0),
    ]
    prompt = build_analysis_prompt(videos, comments)
    assert "Cooking tips" in prompt
    assert "I hate meal planning" in prompt
    assert "50 likes" in prompt


def test_build_analysis_prompt_empty_comments():
    prompt = build_analysis_prompt([], [])
    assert "no" in prompt.lower()


async def test_analyzer_returns_clusters():
    mock_response = MagicMock()
    mock_response.content = [MagicMock()]
    mock_response.content[0].text = json.dumps({
        "clusters": [{
            "theme": "Test pain point", "summary": "People are frustrated",
            "comment_count": 100, "video_count": 2, "potential": "HIGH",
            "app_idea": "Build an app", "sample_comments": ["comment 1", "comment 2"],
        }]
    })
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    with patch("ideascroller.analyzer.AsyncAnthropic", return_value=mock_client):
        analyzer = Analyzer(api_key="test-key")
        result = await analyzer.analyze(
            session_id="test",
            videos=[Video(id="v1", session_id="test", author="u", description="d", comment_count=100, url="url")],
            comments=[Comment(id="c1", video_id="v1", text="comment", author="a", likes=0, reply_count=0)],
        )
    assert len(result.clusters) == 1
    assert result.clusters[0].theme == "Test pain point"
    assert result.clusters[0].potential == "HIGH"


async def test_analyzer_empty_comments():
    with patch("ideascroller.analyzer.AsyncAnthropic"):
        analyzer = Analyzer(api_key="test-key")
        result = await analyzer.analyze(session_id="test", videos=[], comments=[])
    assert len(result.clusters) == 0
