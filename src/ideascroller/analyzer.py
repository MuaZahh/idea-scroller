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
        comment_lines = [f'- "{c.text}" ({c.likes} likes)' for c in video_comments]
        sections.append(header + "\n" + "\n".join(comment_lines))

    return (
        f"Analyze the following {len(comments)} comments from {len(videos)} TikTok videos.\n"
        f"Identify pain points and app opportunities.\n\n"
        + "\n\n".join(sections)
    )


class Analyzer:
    def __init__(self, api_key: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)

    async def analyze(self, session_id: str, videos: list[Video], comments: list[Comment]) -> AnalysisResult:
        if not comments:
            return AnalysisResult(session_id=session_id, clusters=[], raw_response="No comments to analyze")

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
        return AnalysisResult(session_id=session_id, clusters=clusters, raw_response=raw_text)
