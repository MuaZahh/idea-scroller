"""LLM analysis of scraped TikTok comments — supports Anthropic, OpenAI, and Google."""

import json
import logging
import traceback
from typing import Optional

from ideascroller.models import AnalysisCluster, AnalysisResult, Comment, Video

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are an expert product researcher analyzing TikTok comments to discover app and SaaS opportunities.

You will receive comments from TikTok videos grouped by video. Your job is to find the TOP 3 BEST app/SaaS ideas hidden in these comments.

Look for:
- Pain points many people share (frequency matters)
- Frustrations people feel strongly about (intensity matters)
- Problems that could realistically be solved with software
- Ideas where people are literally asking for a solution

HARD FILTER — reject ideas that:
- Are something a normal person WOULD already think to ask ChatGPT (e.g. "explain this to me", "help me write an email", "give me a meal plan", "summarize this"). If the average person's first instinct would be to open ChatGPT for this, it's not an app.
- Already have a well-known app (Shazam, Duolingo, Screen Time, MyFitnessPal, etc.)
- Are too vague or generic ("productivity app", "wellness tracker")

GOOD ideas can use AI/LLMs under the hood — that's fine! The key test is:
"Would a normal non-technical person ACTUALLY think to use ChatGPT for this?"

Examples of GOOD ideas (uses AI but nobody would think to open ChatGPT for it):
- Stamp/coin identifier app (photograph → instant value estimate)
- Plant disease detector (snap a photo of a sick leaf → diagnosis)
- Parking spot finder using real-time camera feeds
- Niche marketplace connecting specific types of people

Examples of BAD ideas (people already use ChatGPT for this):
- "Help me prepare for a job interview" — everyone already does this with ChatGPT
- "Explain medical terms simply" — that's literally a prompt
- "Generate a workout plan" — ChatGPT's most common use case

Ignore off-topic comments, spam, memes, and purely positive reactions.

Return only the SINGLE BEST idea you can find. If there's genuinely a second or third strong one, include those too — but don't pad it. One great idea beats three mediocre ones.

Respond with ONLY valid JSON:
{
  "clusters": [
    {
      "theme": "Short theme title",
      "summary": "2-3 sentence explanation of the pain point and why it's a real opportunity",
      "comment_count": <approximate number of comments mentioning this>,
      "video_count": <number of videos where this came up>,
      "potential": "HIGH",
      "app_idea": "One concrete app concept that solves this",
      "sample_comments": ["3-5 actual comments that show this pain point"]
    }
  ]
}"""

_MERGE_PROMPT = """You are merging multiple batches of pain point analysis from TikTok comments.

Below are the top ideas from each batch. Pick the SINGLE BEST one overall.
- Combine clusters with the same or very similar themes
- Sum up comment_count and video_count for merged clusters
- Keep the best sample_comments
- Include a second or third only if they're genuinely strong — don't pad

Respond with ONLY valid JSON:
{
  "clusters": [
    {
      "theme": "Short theme title",
      "summary": "2-3 sentence explanation",
      "comment_count": <total>,
      "video_count": <total>,
      "potential": "HIGH",
      "app_idea": "One concrete app concept",
      "sample_comments": ["3-5 best comments"]
    }
  ]
}"""

BATCH_SIZE = 50


# ---------------------------------------------------------------------------
# Provider abstraction — each returns a raw text response
# ---------------------------------------------------------------------------

async def _call_anthropic(api_key: str, system: str, user_prompt: str) -> str:
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=api_key)
    response = await client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return response.content[0].text


async def _call_openai(api_key: str, system: str, user_prompt: str) -> str:
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key=api_key)
    response = await client.chat.completions.create(
        model="gpt-4o",
        max_tokens=4096,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user_prompt},
        ],
    )
    return response.choices[0].message.content


async def _call_gemini(api_key: str, system: str, user_prompt: str) -> str:
    from google import genai
    client = genai.Client(api_key=api_key)
    response = await client.aio.models.generate_content(
        model="gemini-2.0-flash",
        contents=f"{system}\n\n{user_prompt}",
    )
    return response.text


def detect_provider(keys: dict[str, str]) -> tuple[str, str, callable]:
    """Detect which provider to use based on which API key is set.

    Returns (provider_name, api_key, call_function).
    """
    if keys.get("ANTHROPIC_API_KEY"):
        return "Anthropic (Claude)", keys["ANTHROPIC_API_KEY"], _call_anthropic
    if keys.get("OPENAI_API_KEY"):
        return "OpenAI (GPT-4o)", keys["OPENAI_API_KEY"], _call_openai
    if keys.get("GEMINI_API_KEY"):
        return "Google (Gemini)", keys["GEMINI_API_KEY"], _call_gemini
    raise ValueError(
        "No API key found. Set one of: ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY in .env"
    )


# ---------------------------------------------------------------------------
# Prompt building and JSON parsing
# ---------------------------------------------------------------------------

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
    """Parse LLM response, stripping markdown fences if present."""
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

    if current_videos:
        batches.append((current_videos, current_comments))

    return batches


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def analyze_comments(
    api_keys: dict[str, str],
    session_id: str,
    videos: list[Video],
    comments: list[Comment],
    on_log: Optional[callable] = None,
) -> AnalysisResult:
    """Analyze comments using whichever LLM provider has a key set.

    api_keys: dict with possible keys ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY
    """
    if not comments:
        return AnalysisResult(
            session_id=session_id, clusters=[], raw_response="No comments to analyze"
        )

    import asyncio as _asyncio
    import inspect as _inspect

    _raw_log = on_log or (lambda msg: logger.info(msg))

    def log(msg: str) -> None:
        result = _raw_log(msg)
        # If the callback is async, schedule it
        if _inspect.isawaitable(result):
            try:
                loop = _asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(result)
                else:
                    loop.run_until_complete(result)
            except RuntimeError:
                pass
    provider_name, api_key, call_fn = detect_provider(api_keys)
    log(f"Using {provider_name} for analysis")

    batches = _chunk_by_video(videos, comments)
    log(f"Analyzing {len(comments)} comments from {len(videos)} videos in {len(batches)} batch(es)")

    all_clusters: list[AnalysisCluster] = []
    raw_responses: list[str] = []

    for i, (batch_videos, batch_comments) in enumerate(batches):
        log(f"  Batch {i + 1}/{len(batches)}: {len(batch_comments)} comments from {len(batch_videos)} videos")
        prompt = build_analysis_prompt(batch_videos, batch_comments)

        try:
            raw_text = await call_fn(api_key, _SYSTEM_PROMPT, prompt)
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

    if len(batches) == 1:
        return AnalysisResult(
            session_id=session_id,
            clusters=all_clusters,
            raw_response=raw_responses[0] if raw_responses else "",
        )

    # Multiple batches — merge
    log(f"Merging {len(all_clusters)} clusters from {len(batches)} batches...")
    merge_input = json.dumps(
        {"clusters": [c.model_dump() for c in all_clusters]}, indent=2,
    )

    try:
        merge_raw = await call_fn(
            api_key, _MERGE_PROMPT, f"Merge these clusters:\n\n{merge_input}"
        )
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
        return AnalysisResult(
            session_id=session_id,
            clusters=all_clusters,
            raw_response="\n---\n".join(raw_responses),
        )
