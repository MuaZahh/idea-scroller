# IdeaScroller

scroll tiktok. find app ideas. that's it.

IdeaScroller watches your TikTok For You page (or any niche), grabs comments from viral videos, and uses AI to figure out what people are actually complaining about — then validates those pain points against real competitors before giving you the ideas worth building.

## how it works

1. opens a browser and scrolls your FYP (or a specific niche like #cooking, #fitness)
2. any video with enough comments? grabs them
3. AI analyzes everything, searches the web for existing competitors, and only returns ideas that survive validation
4. each idea comes with a market rating, competitor list, and why it's still worth building

you get a little web dashboard to watch it work in real time.

## setup

```bash
git clone https://github.com/MuaZahh/idea-scroller.git
cd idea-scroller
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
playwright install chromium
```

### pick your AI provider

you only need ONE of these. use whatever you have:

```bash
pip install -e ".[anthropic]"   # claude (default, recommended)
pip install -e ".[openai]"      # gpt-4o
pip install -e ".[gemini]"      # gemini
```

or just get all of them:

```bash
pip install -e ".[all]"
```

### add your key

create a `.env` file:

```
# pick one
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=AI...
```

## run it

```bash
source .venv/bin/activate
ideascroller
```

open `http://localhost:8000` in your browser. hit start. let it scroll for a bit. hit stop (or ctrl+c) and the AI does its thing.

first time it'll ask you to log into tiktok in the browser window. after that it remembers you.

## features

### niche scrolling

type a topic in the "Niche" field (e.g. `cooking`, `startups`, `fitness`) and it'll scroll that hashtag instead of your FYP. way better signal for specific markets.

### competitor validation

every idea gets validated before you see it:

- the AI searches the web for existing apps/tools that already solve the problem
- rates the market: **OPEN** (nothing exists), **GROWING** (early tools, nobody's nailed it), **CROWDED** (established players)
- explains the **edge** — why this idea is still worth building despite competitors
- ideas in a CROWDED market with no clear differentiator get rejected automatically

### captcha handling

tiktok sometimes throws a rotation captcha when it detects scrolling. ideascroller:

- auto-detects the captcha overlay
- uses opencv to figure out the correct rotation angle
- simulates a human-like slider drag to solve it
- if auto-solve fails, pauses and waits for you to solve it manually in the browser

### strictness modes

controls how critically the AI judges app ideas. pick in the UI dropdown:

| mode | what it does |
|------|-------------|
| **Relaxed** | most permissive — just filters out existing apps and vague ideas. good for brainstorming. |
| **Balanced** | filters out anything a normal person would think to use ChatGPT for. default. |
| **Strict** | extremely critical — the idea must be a very specific niche use case that nobody associates with chatbots. rejects anything mediocre. |

**strict mode might return nothing** even after a long session. that's by design — no result is better than a bad result. if you're getting empty results, try **relaxed** or scroll longer.

### settings

all configurable in the UI or `.env`:

| setting | default | what it does |
|---------|---------|-------------|
| Niche | *(empty)* | leave empty for FYP, or enter a topic/hashtag |
| Min comments | 300 | minimum comments on a video to scrape it |
| Per video | 50 | how many comments to grab per qualifying video |
| Max videos | 0 | auto-stop after this many scraped videos (0 = unlimited) |
| Strictness | balanced | how critically the AI judges ideas |

## what you get

for each idea:

- **theme** — the pain point people are experiencing
- **summary** — why it's a real opportunity
- **app idea** — a concrete concept
- **competitors** — honest list of existing tools that partially solve this
- **market** — OPEN / GROWING / CROWDED
- **edge** — what makes this idea still worth building
- **sample comments** — actual quotes from real people experiencing the problem

you can **export results** as markdown or JSON, and **view past sessions** in the history tab.

## how it actually works (for the nerds)

- **playwright** scrolls tiktok in a real browser with a persistent profile
- **xhr interception** catches tiktok's internal API responses for video metadata and comments
- **opencv** detects and solves rotation captchas via polar cross-correlation
- comments are stored in **sqlite** so nothing gets lost between sessions
- analysis uses **claude's tool_use** — the LLM can search the web mid-analysis to validate competitors
- analysis is **chunked** (50 videos per batch) so it handles long sessions
- multiple batches get **merged** into a final set of validated ideas

## cost

using claude haiku for analysis:

| session size | approx cost |
|-------------|-------------|
| 100 videos, 50 comments each | ~$1-2 |
| 500 videos, 50 comments each | ~$3-5 |
| 3000 videos, 50 comments each | ~$7-8 |

web search validation adds a few extra API calls per batch but uses the same model.

## stack

python, fastapi, playwright, opencv, sqlite, and whichever AI you pick

## license

do whatever you want with it
