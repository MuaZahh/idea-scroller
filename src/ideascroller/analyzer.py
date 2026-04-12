"""LLM analysis of scraped TikTok comments using Anthropic Claude."""

import json
import logging
import traceback
from typing import Optional

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

    async def analyze(
        self,
        session_id: str,
        videos: list[Video],
        comments: list[Comment],
    ) -> AnalysisResult:
        if not comments:
            return AnalysisResult(
                session_id=session_id, clusters=[], raw_response="No comments to analyze"
            )

        prompt = build_analysis_prompt(videos, comments)
        logger.info(
            "Sending %d comments from %d videos to Claude for analysis",
            len(comments),
            len(videos),
        )
        logger.info("Prompt length: %d chars", len(prompt))

        # Retry once on failure
        last_error: Optional[Exception] = None
        for attempt in range(2):
            try:
                response = await self._client.messages.create(
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    system=_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                    timeout=60.0,
                )

                raw_text = response.content[0].text
                logger.info("Claude response: %d chars", len(raw_text))

                # Strip markdown code fences if Claude wrapped the JSON
                json_text = raw_text.strip()
                if json_text.startswith("```"):
                    # Remove opening fence (```json or ```)
                    json_text = json_text.split("\n", 1)[1] if "\n" in json_text else json_text[3:]
                if json_text.endswith("```"):
                    json_text = json_text[:-3]
                json_text = json_text.strip()

                parsed = json.loads(json_text)
                clusters = [AnalysisCluster(**c) for c in parsed["clusters"]]
                return AnalysisResult(
                    session_id=session_id,
                    clusters=clusters,
                    raw_response=raw_text,
                )

            except json.JSONDecodeError as e:
                logger.error("Failed to parse Claude response as JSON: %s", e)
                logger.error("Raw response: %s", raw_text[:500] if raw_text else "empty")
                raise
            except Exception as e:
                last_error = e
                logger.error(
                    "Claude API error (attempt %d/2): %s\n%s",
                    attempt + 1,
                    e,
                    traceback.format_exc(),
                )
                if attempt == 0:
                    logger.info("Retrying in 3 seconds...")
                    import asyncio
                    await asyncio.sleep(3)

        raise last_error  # type: ignore[misc]
