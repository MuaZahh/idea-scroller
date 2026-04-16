"""LLM analysis of scraped TikTok comments — supports Anthropic, OpenAI, and Google."""

import json
import logging
import traceback
from typing import Optional

from ideascroller.models import AnalysisCluster, AnalysisResult, Comment, Video

logger = logging.getLogger(__name__)

_JSON_SCHEMA = """
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
      "competitors": ["List 1-3 existing apps/tools that partially solve this, or empty if none"],
      "market": "OPEN | GROWING | CROWDED",
      "edge": "What specific angle makes this different from existing solutions, or why nothing exists yet",
      "sample_comments": ["3-5 actual comments that show this pain point"]
    }
  ]
}"""

_BASE_PROMPT = """You are an expert product researcher analyzing TikTok comments to discover app and SaaS opportunities.

You will receive comments from TikTok videos grouped by video. Your job is to find the BEST app/SaaS idea hidden in these comments.

Look for:
- Pain points many people share (frequency matters)
- Frustrations people feel strongly about (intensity matters)
- Problems that could realistically be solved with software
- Ideas where people are literally asking for a solution

Ignore off-topic comments, spam, memes, and purely positive reactions.
Return only the SINGLE BEST idea you can find. If there's genuinely a second or third strong one, include those too — but don't pad it. One great idea beats three mediocre ones.

COMPETITOR VALIDATION (critical):
Before including ANY idea in your output, you MUST validate it:
1. Use the web_search tool to search for existing apps/tools (e.g. "plant disease identifier app", "[idea] app competitors")
2. Based on your knowledge AND the search results, list real competitors honestly
3. Rate the market: OPEN (nothing exists), GROWING (a few early/niche tools), CROWDED (established players)
4. If competitors exist, what's the EDGE? Why would someone build this differently?
   - Maybe existing tools are desktop-only and people want mobile
   - Maybe they're expensive and people want a free/simple version
   - Maybe they serve a different audience
   - Maybe they're bloated and people want something focused
5. REJECT ideas where the market is CROWDED and you can't identify a clear edge

COMPETITOR SCALE CHECK (critical — do NOT skip this):
If ANY competitor is a household-name app or has millions of users (e.g. Calm, Headspace, Duolingo, Sleep Cycle, Shazam, MyFitnessPal, Strava), the market is CROWDED. Period.
- Do NOT downgrade to GROWING just because you found a niche angle. Giant apps can add a niche feature in a week.
- "But no one does X specifically" is NOT an edge when the competitors are massive platforms in the same space. They will simply add X.
- A valid edge against large competitors requires a fundamentally different delivery model (hardware, real-time data, local network effects) — not just a UI twist or narrower focus.
- When in doubt, mark CROWDED and reject.

A GROWING market with a clear edge is actually the BEST signal — it means the pain point is validated but nobody's nailed it yet. But GROWING means small/indie competitors, NOT billion-dollar apps that happen to not have one specific feature.

WORKFLOW: scan comments → identify promising pain points → web_search each one → only return ideas that survive validation."""

_MODE_RELAXED = """
FILTER (relaxed):
- Reject ideas that already have a well-known app (Shazam, Duolingo, etc.)
- Reject ideas that are too vague ("productivity app", "wellness tracker")
- It's fine if an LLM could technically do it — focus on whether it's a real pain point people would pay to solve"""

_MODE_BALANCED = """
FILTER (balanced):
- Reject ideas that a normal person WOULD already think to ask ChatGPT for (e.g. "help me write an email", "give me a meal plan"). If the average person's first instinct would be to open ChatGPT for this, it's not an app.
- Reject ideas that already have a well-known app (Shazam, Duolingo, Screen Time, etc.)
- Reject ideas that are too vague or generic

GOOD ideas can use AI under the hood — the test is: "Would a normal person ACTUALLY think to use ChatGPT for this?" If no, it's a valid app idea.

Examples of GOOD: stamp identifier, plant disease detector, parking spot finder
Examples of BAD: interview prep, meal planning, summarizing articles"""

_MODE_STRICT = """
FILTER (very strict):
- The app CAN use AI/LLMs under the hood — that's totally fine
- But reject ideas where a normal person would EVER think "I could just ask ChatGPT this"
- The idea needs a very specific, niche use case that nobody associates with chatbots
- Reject anything that already has a well-known app
- The pain point must appear across many comments — not just a few people

Think of it like this: a stamp identifier app uses AI vision, but NOBODY would open ChatGPT to identify a stamp. That's the bar. The app solves a specific problem in a way that feels like its own thing, not like "talking to an AI."

Examples of GOOD (strict): plant disease scanner, real-time parking finder, niche marketplace, receipt splitter for friend groups
Examples of BAD (strict): writing assistant, study helper, recipe generator, interview prep — even if they have a nice UI, people associate these with ChatGPT

Return NOTHING rather than returning a mediocre idea."""

_MODES = {
    "relaxed": _BASE_PROMPT + _MODE_RELAXED + _JSON_SCHEMA,
    "balanced": _BASE_PROMPT + _MODE_BALANCED + _JSON_SCHEMA,
    "strict": _BASE_PROMPT + _MODE_STRICT + _JSON_SCHEMA,
}

def _get_system_prompt(mode: str = "balanced") -> str:
    return _MODES.get(mode, _MODES["balanced"])

_MERGE_PROMPT = """You are merging multiple batches of pain point analysis from TikTok comments.

Below are the top ideas from each batch. Pick the SINGLE BEST one overall.
- Combine clusters with the same or very similar themes
- Sum up comment_count and video_count for merged clusters
- Keep the best sample_comments
- Merge competitor lists — remove duplicates
- Pick the most accurate market rating
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
      "competitors": ["existing apps/tools"],
      "market": "OPEN | GROWING | CROWDED",
      "edge": "What makes this different",
      "sample_comments": ["3-5 best comments"]
    }
  ]
}"""

BATCH_SIZE = 50


# ---------------------------------------------------------------------------
# Provider abstraction — each returns a raw text response
# ---------------------------------------------------------------------------

_SEARCH_TOOL = {
    "name": "web_search",
    "description": (
        "Search the web to verify whether an app idea already exists, "
        "find competitors, and check market saturation. "
        "Use this BEFORE including any idea in your final output."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query, e.g. 'plant disease identifier app' or 'receipt splitting app competitors'",
            }
        },
        "required": ["query"],
    },
}


async def _web_search(query: str) -> str:
    """Run a web search using DuckDuckGo (no API key needed)."""
    import urllib.parse
    import httpx

    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            text = resp.text
            # Extract result snippets from DDG HTML
            results: list[str] = []
            for block in text.split('class="result__snippet"'):
                if len(results) >= 5:
                    break
                if block.find("</a>") > -1 or block.find("</span>") > -1:
                    # Grab text between > and <
                    import re
                    snippet = re.sub(r"<[^>]+>", "", block[:500]).strip()
                    if snippet and len(snippet) > 20:
                        results.append(snippet[:200])
            return "\n".join(results) if results else "No results found."
    except Exception as e:
        return f"Search failed: {e}"


async def _call_anthropic(api_key: str, system: str, user_prompt: str) -> str:
    from anthropic import AsyncAnthropic
    client = AsyncAnthropic(api_key=api_key)

    messages = [{"role": "user", "content": user_prompt}]

    # First call — may request tool use
    response = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        system=system,
        messages=messages,
        tools=[_SEARCH_TOOL],
    )

    # Handle tool use loop (max 5 searches per batch)
    for _ in range(5):
        if response.stop_reason != "tool_use":
            break

        # Process all tool calls in the response
        tool_results = []
        for block in response.content:
            if block.type == "tool_use" and block.name == "web_search":
                logger.info("LLM searching: %s", block.input.get("query", ""))
                result = await _web_search(block.input["query"])
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })

        if not tool_results:
            break

        messages = [
            *messages,
            {"role": "assistant", "content": response.content},
            *[{"role": "user", "content": [tr]} for tr in tool_results],
        ]

        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system=system,
            messages=messages,
            tools=[_SEARCH_TOOL],
        )

    # Extract final text
    for block in response.content:
        if hasattr(block, "text"):
            return block.text
    return ""


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
    """Parse LLM response, extracting JSON even when surrounded by extra text."""
    text = raw_text.strip()
    if not text:
        return {"clusters": []}

    # Strip markdown fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    if not text:
        return {"clusters": []}

    # Try direct parse first
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Find the first { ... } or [ ... ] block in the text
        start = -1
        brace = None
        for i, ch in enumerate(text):
            if ch in "{[":
                start = i
                brace = "}" if ch == "{" else "]"
                break

        if start == -1:
            logger.error("No JSON found in LLM response: %s", text[:200])
            return {"clusters": []}

        # Walk forward to find the matching closing brace
        depth = 0
        end = start
        for i in range(start, len(text)):
            if text[i] == brace.replace("}", "{").replace("]", "["):
                # opening brace of same type
                pass
            if text[i] in "{[":
                depth += 1
            elif text[i] in "}]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        try:
            parsed = json.loads(text[start:end])
        except json.JSONDecodeError:
            logger.error("Failed to parse extracted JSON: %s", text[start:end][:200])
            return {"clusters": []}

    if isinstance(parsed, list):
        return {"clusters": parsed}
    if isinstance(parsed, dict) and "clusters" not in parsed:
        if "theme" in parsed:
            return {"clusters": [parsed]}
        return {"clusters": []}
    if isinstance(parsed, dict):
        return parsed
    return {"clusters": []}


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
    mode: str = "balanced",
) -> AnalysisResult:
    """Analyze comments using whichever LLM provider has a key set.

    mode: "relaxed", "balanced", or "strict" — controls how critically ideas are filtered.
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
    system_prompt = _get_system_prompt(mode)
    log(f"Using {provider_name} for analysis (mode: {mode})")

    batches = _chunk_by_video(videos, comments)
    log(f"Analyzing {len(comments)} comments from {len(videos)} videos in {len(batches)} batch(es)")

    all_clusters: list[AnalysisCluster] = []
    raw_responses: list[str] = []

    for i, (batch_videos, batch_comments) in enumerate(batches):
        log(f"  Batch {i + 1}/{len(batches)}: {len(batch_comments)} comments from {len(batch_videos)} videos")
        prompt = build_analysis_prompt(batch_videos, batch_comments)

        for attempt in range(2):
            try:
                raw_text = await call_fn(api_key, system_prompt, prompt)
                raw_responses.append(raw_text)
                parsed = _parse_json_response(raw_text)
                batch_clusters = []
                for c in parsed["clusters"]:
                    try:
                        batch_clusters.append(AnalysisCluster(**c))
                    except Exception as ce:
                        logger.debug("Skipping malformed cluster: %s", ce)
                all_clusters.extend(batch_clusters)
                log(f"  Batch {i + 1}: found {len(batch_clusters)} clusters")
                break
            except Exception as e:
                if attempt == 0:
                    logger.debug("Batch %d attempt 1 failed: %s, retrying...", i + 1, e)
                else:
                    logger.error("Batch %d failed: %s\nRaw response: %s", i + 1, e, raw_text[:500] if 'raw_text' in dir() else 'N/A')
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
