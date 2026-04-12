"""LLM analysis of scraped TikTok comments using Anthropic Claude."""

import asyncio
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

_MERGE_PROMPT = """You are merging multiple batches of pain point analysis from TikTok comments.

Below are clusters identified from different batches. Merge them:
- Combine clusters with the same or very similar themes
- Sum up comment_count and video_count for merged clusters
- Keep the best sample_comments from each
- Re-rank by overall frequency and intensity
- Keep potential ratings accurate based on merged totals

Respond with ONLY valid JSON matching the same schema:
{
  "clusters": [
    {
      "theme": "Short theme title",
      "summary": "2-3 sentence explanation",
      "comment_count": <total across merged clusters>,
      "video_count": <total across merged clusters>,
      "potential": "HIGH" | "MEDIUM" | "LOW",
      "app_idea": "One-sentence app concept",
      "sample_comments": ["3-5 best representative comments"]
    }
  ]
}"""

# Max ~50 videos per batch to stay well within context limits
BATCH_SIZE = 50


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


def _parse_json_response(raw_text: str) -> dict:
    """Parse Claude's response, stripping markdown fences if present."""
    json_text = raw_text.strip()
    if json_text.startswith("```"):
        json_text = json_text.split("\n", 1)[1] if "\n" in json_text else json_text[3:]
    if json_text.endswith("```"):
        json_text = json_text[:-3]
    return json.loads(json_text.strip())


def _chunk_by_video(
    videos: list[Video], comments: list[Comment], batch_size: int = BATCH_SIZE,
) -> list[tuple[list[Video], list[Comment]]]:
    """Split videos and their comments into batches."""
    comments_by_video: dict[str, list[Comment]] = {}
    for c in comments:
        comments_by_video.setdefault(c.video_id, []).append(c)

    batches: list[tuple[list[Video], list[Comment]]] = []
    current_videos: list[Video] = []
    current_comments: list[Comment] = []

    for video in videos:
        current_videos.append(video)
        current_comments.extend(comments_by_video.get(video.id, []))

        if len(current_videos) >= batch_size:
            batches.append((list(current_videos), list(current_comments)))
            current_videos = []
            current_comments = []

    # Don't forget the last batch
    if current_videos:
        batches.append((current_videos, current_comments))

    return batches


async def analyze_comments(
    api_key: str,
    session_id: str,
    videos: list[Video],
    comments: list[Comment],
    on_log: Optional[callable] = None,
) -> AnalysisResult:
    """Analyze comments — chunks automatically if there are too many videos."""
    if not comments:
        return AnalysisResult(
            session_id=session_id, clusters=[], raw_response="No comments to analyze"
        )

    log = on_log or (lambda msg: logger.info(msg))
    client = AsyncAnthropic(api_key=api_key)

    batches = _chunk_by_video(videos, comments)
    log(f"Analyzing {len(comments)} comments from {len(videos)} videos in {len(batches)} batch(es)")

    all_clusters: list[AnalysisCluster] = []
    raw_responses: list[str] = []

    for i, (batch_videos, batch_comments) in enumerate(batches):
        log(f"  Batch {i + 1}/{len(batches)}: {len(batch_comments)} comments from {len(batch_videos)} videos")

        prompt = build_analysis_prompt(batch_videos, batch_comments)

        try:
            response = await client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4096,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.content[0].text
            raw_responses.append(raw_text)

            parsed = _parse_json_response(raw_text)
            batch_clusters = [AnalysisCluster(**c) for c in parsed["clusters"]]
            all_clusters.extend(batch_clusters)
            log(f"  Batch {i + 1}: found {len(batch_clusters)} clusters")

        except Exception as e:
            logger.error("Batch %d failed: %s\n%s", i + 1, e, traceback.format_exc())
            log(f"  Batch {i + 1} failed: {e}")

    if not all_clusters:
        return AnalysisResult(
            session_id=session_id, clusters=[], raw_response="All batches failed"
        )

    # If only one batch, no merge needed
    if len(batches) == 1:
        return AnalysisResult(
            session_id=session_id,
            clusters=all_clusters,
            raw_response=raw_responses[0] if raw_responses else "",
        )

    # Multiple batches — merge the clusters
    log(f"Merging {len(all_clusters)} clusters from {len(batches)} batches...")

    merge_input = json.dumps(
        {"clusters": [c.model_dump() for c in all_clusters]},
        indent=2,
    )

    try:
        merge_response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=_MERGE_PROMPT,
            messages=[{"role": "user", "content": f"Merge these clusters:\n\n{merge_input}"}],
        )
        merge_raw = merge_response.content[0].text
        parsed = _parse_json_response(merge_raw)
        merged_clusters = [AnalysisCluster(**c) for c in parsed["clusters"]]
        log(f"Merged into {len(merged_clusters)} final clusters")

        return AnalysisResult(
            session_id=session_id,
            clusters=merged_clusters,
            raw_response=merge_raw,
        )
    except Exception as e:
        logger.error("Merge failed: %s", e)
        log(f"Merge failed, returning unmerged clusters: {e}")
        # Return unmerged clusters as fallback
        return AnalysisResult(
            session_id=session_id,
            clusters=all_clusters,
            raw_response="\n---\n".join(raw_responses),
        )
