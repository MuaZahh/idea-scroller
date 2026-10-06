"""Adaptive validator: Claude Sonnet 4.6 with adaptive thinking + 10 tools.

Replaces the rigid fan-out orchestrator for the validation phase. Claude reads
each cluster, writes a search_plan grounded in sample_comments, then issues
personalized queries across TrustMRR / Reddit / Google Autocomplete / Wikipedia
— using interleaved thinking to refine between tool results.

Design is directly from the 4-agent research pass:
- search_plan tool required FIRST (forces sample_comments grounding)
- think tool (τ-Bench pattern, 54% benchmark lift)
- Every search tool requires `reasoning` + `angle` enum (machine-checkable diversity)
- Plain declarative prompt (4.6 overtriggers on "CRITICAL/MUST")
- sample_comments placed at TOP of user message (long-context anchoring)
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from anthropic import AsyncAnthropic

from ideascroller.models import AnalysisCluster, ValidationSignals, Video
from ideascroller.validators.keywords import check_keyword_async
from ideascroller.validators.reddit import check_pain_density, search_reddit
from ideascroller.validators.trustmrr import (
    load_cached_corpus,
    match_competitors,
)

logger = logging.getLogger(__name__)

ADAPTIVE_MODEL = "claude-sonnet-4-6"
MAX_TOOL_ITERATIONS = 15
MAX_TOKENS = 16_000


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_plan",
        "description": (
            "Record your validation plan for this idea before issuing any other tool. "
            "You must call this tool exactly once, as your very first action. "
            "The plan grounds your subsequent searches in the actual user language from "
            "sample_comments. Downstream reviewers check your plan against your queries — "
            "if verbatim_user_phrases is empty or generic, the verdict is rejected."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "verbatim_user_phrases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "3-5 EXACT phrases copied from sample_comments that capture the "
                        "real pain. Do not paraphrase. These phrases drive your searches."
                    ),
                },
                "named_competitors_to_probe": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Specific competitor names you plan to investigate, drawn from "
                        "the cluster's competitors field, sample_comments, or your own "
                        "knowledge of the niche."
                    ),
                },
                "chosen_subreddits": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "2-6 niche subreddits where practitioners of this pain actually "
                        "post. Prefer specific over generic: r/loseit over r/fitness; "
                        "r/MacroFactor over r/apps. Do NOT pass r/all."
                    ),
                },
                "hypotheses": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "2-3 competing hypotheses about whether this pain is real, "
                        "severe, and underserved. You will seek evidence for and against each."
                    ),
                },
                "disconfirming_evidence_i_will_look_for": {
                    "type": "string",
                    "description": (
                        "What would change your mind about this being a good idea? "
                        "If you cannot name it, the idea cannot be falsified."
                    ),
                },
            },
            "required": [
                "verbatim_user_phrases",
                "named_competitors_to_probe",
                "chosen_subreddits",
                "hypotheses",
                "disconfirming_evidence_i_will_look_for",
            ],
        },
    },
    {
        "name": "think",
        "description": (
            "Append a thought to your scratchpad. Use this between tool calls to reflect "
            "on what you just learned, identify gaps, and plan your next query. This tool "
            "does not touch the network. Use it liberally — it is free."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thought": {"type": "string"},
            },
            "required": ["thought"],
        },
    },
    {
        "name": "reddit_search",
        "description": (
            "Search Reddit for authentic user discussions. Prefer niche subreddits over "
            "generic ones — r/loseit over r/fitness, r/MacroFactor over r/apps. Three "
            "searches from different angles beat one broad search.\n\n"
            "IMPORTANT: Keep queries SHORT (2-4 keywords). Reddit's search dilutes relevance "
            "on long queries. The subreddits list is auto-appended to your query as a "
            "restriction — you don't need to include sub names in the query string.\n\n"
            "GOOD query: 'barcode wrong' subreddits=['loseit','MacroFactor']\n"
            "GOOD query: '\"i wish there was\" calorie' subreddits=['loseit']\n"
            "BAD query: 'MyFitnessPal database wrong barcode scan frustrating alternatives' (too long)\n"
            "BAD query: 'fitness app issues' subreddits=['fitness'] (too generic)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "Reddit search string. Must contain either (a) a verbatim fragment "
                        "from sample_comments, (b) a named competitor + a specific complaint, "
                        "or (c) a niche practitioner term. Use double quotes for exact phrase "
                        "matching: \"i wish there was\""
                    ),
                },
                "subreddits": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "2-4 specific subreddits chosen for THIS idea (without r/ prefix). "
                        "Must be justified by sample_comments or competitor list."
                    ),
                },
                "angle": {
                    "type": "string",
                    "enum": [
                        "user_language",
                        "competitor_friction",
                        "category_term",
                        "negative_complaint",
                    ],
                    "description": (
                        "Which search angle this query covers. Across all your reddit_search "
                        "calls, cover at least 3 distinct angles."
                    ),
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "One sentence: why this specific query will surface real user pain. "
                        "Reference the sample_comment or competitor that motivated it."
                    ),
                },
            },
            "required": ["query", "subreddits", "angle", "reasoning"],
        },
    },
    {
        "name": "reddit_find_subreddits",
        "description": (
            "Discover subreddits matching a topic when you don't know which communities "
            "discuss an idea. Returns list of {name, subscribers, description}. Use BEFORE "
            "reddit_search when the niche is unfamiliar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "limit": {"type": "integer", "default": 10, "minimum": 1, "maximum": 25},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "trustmrr_search",
        "description": (
            "Search the TrustMRR corpus of verified-revenue startups using fuzzy matching "
            "against name + description. Returns top matches with real MRR in USD. Use to "
            "find competitors making real money in the same space.\n\n"
            "GOOD: keyword='AI calorie tracker', category_hint='health-fitness'\n"
            "BAD: keyword='app' (too generic), category_hint='ai' (too broad)"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": (
                        "Concept to search for. Prefer 2-4 words that would appear in a "
                        "real startup's description."
                    ),
                },
                "category_hint": {
                    "type": "string",
                    "description": (
                        "Optional. One of the 31 TrustMRR categories. Call "
                        "trustmrr_list_categories first if unsure. Boosts matches in that "
                        "category; does not exclude others."
                    ),
                },
                "min_mrr_usd": {
                    "type": "integer",
                    "description": "Optional minimum MRR filter in USD (not cents).",
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "Why this keyword/category combo — what competitor type are you "
                        "hunting?"
                    ),
                },
            },
            "required": ["keyword", "reasoning"],
        },
    },
    {
        "name": "trustmrr_list_categories",
        "description": (
            "List all valid TrustMRR category strings with counts. Call this before "
            "trustmrr_search if you're unsure which category_hint to use."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "google_autocomplete",
        "description": (
            "Query Google Autocomplete for suggestions. Use this to gauge whether users "
            "actually search for a concept. The presence of the seed phrase as a suggestion "
            "indicates real search interest. Also surfaces rising related queries."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "seed_phrase": {
                    "type": "string",
                    "description": (
                        "A phrase users might type. Prefer user-language fragments from "
                        "sample_comments over marketing phrases."
                    ),
                },
                "reasoning": {"type": "string"},
            },
            "required": ["seed_phrase", "reasoning"],
        },
    },
]


# ---------------------------------------------------------------------------
# System prompt — from the research. Plain declarative voice, NO "CRITICAL/MUST".
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are IdeaScroller's Validator. Your job is to judge whether a TikTok-surfaced app idea represents a real, underserved pain — by finding evidence in external sources, not by reasoning from your training data.

The single most important rule: every search you issue must be grounded in the specific language users actually used in sample_comments. The theme and app_idea fields are AI-generated labels — they are a starting point, not the truth. Your searches surface truth; lazy searches surface AI slop.

<research_contract>
For each idea you validate:
1. Call search_plan FIRST, before any other tool. Ground it in sample_comments verbatim.
2. Extract 3-5 verbatim phrases from sample_comments that represent the actual pain.
3. For each search tool, issue queries covering at least 3 distinct angles:
   a. User-language angle — an exact phrase lifted from sample_comments
   b. Competitor-friction angle — a specific complaint about a named competitor
   c. Category/topic angle — the broader niche term a practitioner would use
4. Use the think tool between tool results to reflect on gaps and plan the next query.
5. Issue parallel tool calls whenever the queries are independent.
6. Reject your own first-draft queries once before issuing them. If a query could have been written without reading sample_comments, rewrite it.
</research_contract>

<query_quality_bar>
A GOOD query contains a verbatim fragment from a user comment, a named competitor, or a niche term a practitioner would use.
Example: "MacroFactor vs Cronometer barcode accuracy"
Example: "MFP scanner wrong database" subreddits=["loseit","MacroFactor"]

A BAD query is a restatement of the theme.
Example: "photo calorie counter app" — too generic
Example: "fitness tracking pain points" — reads like a product category
</query_quality_bar>

<subreddit_selection>
Do not search r/all or r/apps. For each idea, identify 2-4 niche subreddits where practitioners of this pain actually post. Derive these from sample_comments and the competitor list, not from generic priors. If the idea is fitness-adjacent, r/loseit and r/MacroFactor beat r/fitness. If it's pet-adjacent, r/dogs or r/cats beats r/pets.
</subreddit_selection>

<parallel_tool_calls>
If you intend to call multiple tools and there are no dependencies between them, make all the independent tool calls in parallel. Three reddit_search calls covering three different angles should fire in one turn, not three. Never use placeholders or guess missing parameters.
</parallel_tool_calls>

<reflection_after_tools>
After each batch of tool results, use the think tool to:
- Quote the strongest evidence you found (1-2 sentences verbatim).
- Identify what's still missing for a confident verdict.
- Decide the next search batch, or declare sufficient evidence.
Do not write the final verdict until you have at least 2 independent pieces of evidence that corroborate or refute each of: pain reality, pain severity, competitor saturation, market size.
</reflection_after_tools>

<output_contract>
When you have gathered enough evidence, stop calling tools and write your final verdict as JSON with this exact shape:
{
  "verdict": "strong" | "viable" | "weak" | "absent",
  "confidence": 0-100,
  "pain_evidence": [{"source": "reddit|autocomplete", "verbatim_quote": "...", "url": "..."}],
  "competitor_evidence": [{"name": "...", "mrr_usd": 0, "url": "..."}],
  "market_evidence": {"wikipedia_monthly_views": null, "autocomplete_suggestion_count": 0},
  "rationale": "one sentence — why this verdict."
}

Every claim must be backed by a verbatim_quote + URL you found via a tool call. Claims without evidence will be flagged.
</output_contract>
"""


# ---------------------------------------------------------------------------
# Tool handlers — dispatch from Claude tool_use to real validator code
# ---------------------------------------------------------------------------


@dataclass
class RunTrace:
    """What we record about a validator run, for post-hoc evaluation."""

    cluster_theme: str
    plan: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    final_verdict: dict[str, Any] | None = None
    turns: int = 0
    errors: list[str] = field(default_factory=list)


async def _handle_reddit_search(args: dict[str, Any]) -> dict[str, Any]:
    query = args.get("query", "")
    subreddits = args.get("subreddits") or []
    # Push subreddit restriction into the Reddit query itself — Reddit's search
    # supports (subreddit:X OR subreddit:Y) inline. This is far more reliable
    # than post-filtering because Reddit's relevance ranker incorporates the
    # sub restriction when scoring.
    if subreddits:
        sub_filter = " OR ".join(f"subreddit:{s.lower()}" for s in subreddits[:8])
        full_query = f"({query}) ({sub_filter})"
    else:
        full_query = query
    threads = await search_reddit(full_query, limit=25)
    return {
        "query": query,
        "effective_query": full_query,
        "subreddits_requested": subreddits,
        "thread_count": len(threads),
        "threads": [
            {
                "title": t.title,
                "subreddit": t.subreddit,
                "url": t.url,
                "score": t.score,
                "num_comments": t.num_comments,
                "selftext_preview": t.selftext_preview,
            }
            for t in threads[:10]
        ],
    }


async def _handle_reddit_find_subreddits(args: dict[str, Any]) -> dict[str, Any]:
    import httpx

    topic = args.get("topic", "")
    limit = int(args.get("limit", 10))
    url = f"https://www.reddit.com/subreddits/search.json?q={topic}&limit={limit}"
    try:
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "IdeaScroller/1.0"}) as c:
            r = await c.get(url)
            if r.status_code >= 400:
                return {"error": f"HTTP {r.status_code}", "subreddits": []}
            data = r.json()
        children = ((data.get("data") or {}).get("children")) or []
        return {
            "topic": topic,
            "subreddits": [
                {
                    "name": (ch.get("data") or {}).get("display_name"),
                    "subscribers": (ch.get("data") or {}).get("subscribers", 0),
                    "description": ((ch.get("data") or {}).get("public_description") or "")[:200],
                }
                for ch in children[:limit]
                if (ch.get("data") or {}).get("display_name")
            ],
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}", "subreddits": []}


async def _handle_trustmrr_search(args: dict[str, Any]) -> dict[str, Any]:
    keyword = args.get("keyword", "")
    category_hint = args.get("category_hint") or ""
    min_mrr = int(args.get("min_mrr_usd") or 0)
    corpus = load_cached_corpus()
    matches = match_competitors(
        keyword,
        corpus,
        category_boost=(category_hint,) if category_hint else (),
        top_k=10,
    )
    out = []
    for m in matches:
        if m.competitor.mrr_usd < min_mrr:
            continue
        out.append({
            "name": m.competitor.name,
            "slug": m.competitor.slug,
            "description": m.competitor.description,
            "website": m.competitor.website,
            "category": m.competitor.category,
            "mrr_usd": m.competitor.mrr_usd,
            "url": m.competitor.url,
            "match_score": round(m.score, 1),
            "matched_on": m.matched_on,
        })
    return {"keyword": keyword, "count": len(out), "competitors": out}


async def _handle_trustmrr_list_categories(args: dict[str, Any]) -> dict[str, Any]:
    corpus = load_cached_corpus()
    counts: dict[str, int] = {}
    for r in corpus:
        cat = r.get("category") or "unknown"
        counts[cat] = counts.get(cat, 0) + 1
    top = sorted(counts.items(), key=lambda x: -x[1])
    return {
        "total_startups": len(corpus),
        "categories": [{"name": c, "count": n} for c, n in top],
    }


async def _handle_google_autocomplete(args: dict[str, Any]) -> dict[str, Any]:
    seed = args.get("seed_phrase", "")
    signal = await check_keyword_async(seed)
    return {
        "seed_phrase": seed,
        "suggestion_count": signal.suggestion_count,
        "exact_phrase_is_suggestion": signal.exact_phrase_is_suggestion,
        "suggestions": list(signal.autocomplete_suggestions),
        "wikipedia_monthly_views": signal.wikipedia_monthly_views,
        "wikipedia_article_title": signal.wikipedia_article_title,
    }


TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "reddit_search": _handle_reddit_search,
    "reddit_find_subreddits": _handle_reddit_find_subreddits,
    "trustmrr_search": _handle_trustmrr_search,
    "trustmrr_list_categories": _handle_trustmrr_list_categories,
    "google_autocomplete": _handle_google_autocomplete,
}


# ---------------------------------------------------------------------------
# Main agentic loop
# ---------------------------------------------------------------------------


def _format_user_message(cluster: AnalysisCluster) -> str:
    """Place sample_comments at TOP (long-context anchoring rule)."""
    sample_block = "\n".join(f"- {c}" for c in cluster.sample_comments or [])
    return f"""<sample_comments>
{sample_block or "(none)"}
</sample_comments>

<cluster>
theme: {cluster.theme}
app_idea: {cluster.app_idea}
market: {cluster.market}
edge: {cluster.edge}
comment_count: {cluster.comment_count}
video_count: {cluster.video_count}
</cluster>

<existing_competitors>
{", ".join(cluster.competitors) if cluster.competitors else "(none named by analysis)"}
</existing_competitors>

Validate this idea. Start with search_plan."""


async def _dispatch_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool call; returns the tool result dict (never raises)."""
    try:
        if name == "search_plan":
            return {"plan_recorded": True, **args}
        if name == "think":
            return {"thought_recorded": True}
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return {"error": f"unknown tool: {name}"}
        return await handler(args)
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        return {"error": f"{type(exc).__name__}: {exc}"}


async def validate_cluster(
    api_key: str,
    cluster: AnalysisCluster,
    *,
    log: Callable[[str], None] | None = None,
) -> tuple[AnalysisCluster, RunTrace]:
    """Run the adaptive validator loop on one cluster.

    Returns the enriched cluster + a trace of what happened.
    """
    _log = log or (lambda msg: logger.info(msg))
    trace = RunTrace(cluster_theme=cluster.theme)
    client = AsyncAnthropic(api_key=api_key)

    user_content = _format_user_message(cluster)
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]

    for turn in range(MAX_TOOL_ITERATIONS):
        trace.turns = turn + 1
        try:
            response = await client.messages.create(
                model=ADAPTIVE_MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )
        except Exception as exc:
            trace.errors.append(f"api_error_turn_{turn}: {exc}")
            _log(f"  API error on turn {turn}: {exc}")
            break

        # Collect tool_use blocks this turn
        tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        text_blocks = [b for b in response.content if getattr(b, "type", None) == "text"]

        if response.stop_reason != "tool_use" or not tool_uses:
            # Extract final verdict from text
            for tb in text_blocks:
                txt = getattr(tb, "text", "") or ""
                # Look for JSON block
                start = txt.find("{")
                end = txt.rfind("}")
                if start >= 0 and end > start:
                    try:
                        trace.final_verdict = json.loads(txt[start:end + 1])
                        break
                    except json.JSONDecodeError:
                        continue
            _log(f"  Done after {turn + 1} turns · verdict={trace.final_verdict.get('verdict') if trace.final_verdict else 'unparsed'}")
            break

        # Execute tool calls in parallel
        _log(f"  Turn {turn + 1}: Claude requested {len(tool_uses)} tool call(s): {[t.name for t in tool_uses]}")
        tasks = [_dispatch_tool(t.name, dict(t.input)) for t in tool_uses]
        results = await asyncio.gather(*tasks)

        # Record in trace
        for t, r in zip(tool_uses, results):
            trace.tool_calls.append({"tool": t.name, "input": dict(t.input), "result_summary": _summarize(r)})
            if t.name == "search_plan":
                trace.plan = dict(t.input)

        # Append assistant turn + tool_results
        messages.append({"role": "assistant", "content": response.content})
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": t.id,
                    "content": json.dumps(r)[:8000],  # cap in case of huge results
                    **({"is_error": True} if isinstance(r, dict) and r.get("error") else {}),
                }
                for t, r in zip(tool_uses, results)
            ],
        })
    else:
        trace.errors.append(f"hit max iterations ({MAX_TOOL_ITERATIONS})")
        _log(f"  Hit max iterations — forcing stop")

    enriched = _cluster_from_verdict(cluster, trace)
    return enriched, trace


def _summarize(result: dict[str, Any]) -> str:
    if "error" in result:
        return f"error: {result['error']}"
    if "threads" in result:
        return f"{result.get('thread_count', 0)} threads"
    if "competitors" in result:
        return f"{result.get('count', 0)} competitors"
    if "subreddits" in result:
        return f"{len(result.get('subreddits', []))} subreddits"
    if "categories" in result:
        return f"{len(result.get('categories', []))} categories"
    if "suggestions" in result:
        return f"{result.get('suggestion_count', 0)} suggestions"
    if "plan_recorded" in result:
        return "plan recorded"
    if "thought_recorded" in result:
        return "thought recorded"
    return "ok"


def _cluster_from_verdict(cluster: AnalysisCluster, trace: RunTrace) -> AnalysisCluster:
    """Fold the Claude verdict into ValidationSignals matching the existing UI schema."""
    verdict = trace.final_verdict or {}
    overall = verdict.get("verdict") or "unchecked"

    # Competitor evidence → top_competitors (UI-compatible shape)
    comp_ev = verdict.get("competitor_evidence") or []
    top_comps = [
        {
            "name": c.get("name", "?"),
            "slug": c.get("slug", ""),
            "description": c.get("description", ""),
            "website": c.get("url") or c.get("website"),
            "category": c.get("category", ""),
            "mrr_usd": float(c.get("mrr_usd") or 0),
            "total_revenue_usd": 0.0,
            "growth_mrr_30d": None,
            "customers": None,
            "url": c.get("url") or c.get("website") or "",
        }
        for c in comp_ev
    ]
    max_mrr = max((c["mrr_usd"] for c in top_comps), default=0.0)
    above_10k = sum(1 for c in top_comps if c["mrr_usd"] >= 10_000)
    above_100k = sum(1 for c in top_comps if c["mrr_usd"] >= 100_000)

    # Pain evidence → top_threads + derived Reddit signal
    pain_ev = verdict.get("pain_evidence") or []
    reddit_hits = [p for p in pain_ev if p.get("source") == "reddit"]
    top_threads = [
        {
            "title": (p.get("verbatim_quote") or "")[:120],
            "subreddit": _extract_subreddit(p.get("url", "")),
            "url": p.get("url", ""),
            "score": 0,
            "num_comments": 0,
            "created_utc": 0.0,
            "selftext_preview": (p.get("verbatim_quote") or "")[:240],
        }
        for p in reddit_hits
    ]

    market_ev = verdict.get("market_evidence") or {}

    signals = ValidationSignals(
        trustmrr={
            "checked": True,
            "error": None,
            "verdict": overall,
            "verdict_source": "adaptive_llm",
            "top_competitors": top_comps,
            "max_mrr_usd": max_mrr,
            "competitors_above_10k_mrr": above_10k,
            "competitors_above_100k_mrr": above_100k,
            "category": None,
            "turns": trace.turns,
            "rationale": verdict.get("rationale", ""),
        },
        reddit={
            "checked": True,
            "error": None,
            "verdict": overall,
            "total_threads_found": len(reddit_hits),
            "top_threads": top_threads,
            "total_upvotes": 0,
            "total_comments": 0,
            "subreddits": list({t["subreddit"] for t in top_threads if t["subreddit"]}),
        },
        keyword={
            "checked": True,
            "error": None,
            "keyword": (trace.plan or {}).get("verbatim_user_phrases", [""])[0] if trace.plan else "",
            "verdict": overall,
            "autocomplete_suggestions": tuple(),
            "suggestion_count": int(market_ev.get("autocomplete_suggestion_count") or 0),
            "exact_phrase_is_suggestion": False,
            "wikipedia_monthly_views": market_ev.get("wikipedia_monthly_views"),
            "wikipedia_article_title": None,
            "search_volume": None,
            "cpc_usd": None,
            "source": "adaptive_llm",
        },
        virality=None,
    )
    return cluster.model_copy(update={"validation": signals})


def _extract_subreddit(url: str) -> str:
    """Pull subreddit name out of a reddit.com/r/X/... url."""
    import re
    m = re.search(r"reddit\.com/r/([^/]+)", url or "")
    return m.group(1) if m else ""


async def validate_clusters_adaptive(
    api_key: str,
    clusters: list[AnalysisCluster],
    videos: list[Video],
    *,
    log: Callable[[str], None] | None = None,
) -> tuple[list[AnalysisCluster], list[RunTrace]]:
    """Parallel: each cluster gets its own agentic loop."""
    from ideascroller.validators.orchestrator import compute_session_virality

    virality = compute_session_virality(videos)
    (log or (lambda m: None))(f"Running adaptive validator on {len(clusters)} cluster(s) (virality: {virality})")

    results = await asyncio.gather(*[
        validate_cluster(api_key, c, log=log) for c in clusters
    ])
    enriched_clusters: list[AnalysisCluster] = []
    traces: list[RunTrace] = []
    for c, t in results:
        if c.validation:
            vdict = c.validation.model_dump()
            vdict["virality"] = virality
            enriched_clusters.append(c.model_copy(update={"validation": ValidationSignals(**vdict)}))
        else:
            enriched_clusters.append(c)
        traces.append(t)
    return enriched_clusters, traces
