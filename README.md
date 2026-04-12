# IdeaScroller

scroll tiktok. find app ideas. that's it.

IdeaScroller watches your TikTok For You page, grabs comments from viral videos, and uses AI to figure out what people are actually complaining about — then turns those pain points into app ideas.

## how it works

1. opens a browser and scrolls your FYP
2. any video with 300+ comments? grabs the comments
3. when you stop, AI analyzes everything and gives you the **top 3 app ideas** it found

you get a little web dashboard to watch it work in real time.

## setup

```bash
git clone https://github.com/MuaZahh/idea-scroller.git
cd idea-scroller
pip install -e .
playwright install chromium
```

### pick your AI provider

you only need ONE of these. use whatever you have:

```bash
pip install -e ".[anthropic]"   # claude
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
ideascroller
```

open `http://localhost:8000` in your browser. hit start. let it scroll for a bit. hit stop (or ctrl+c) and the AI does its thing.

first time it'll ask you to log into tiktok in the browser window. after that it remembers you.

### strictness modes

controls how critically the AI judges app ideas. pick in the UI dropdown:

| mode | what it does |
|------|-------------|
| **Relaxed** | most permissive — just filters out existing apps and vague ideas. good for brainstorming. |
| **Balanced** | filters out anything a normal person would think to use ChatGPT for. default. |
| **Strict** | extremely critical — idea must require real-time data, multi-user coordination, or hardware integration. rejects anything a chatbot could do. |

**strict mode might return nothing** even after a long session. that's by design — no result is better than a bad result. if you're getting empty results, try **relaxed** or scroll longer.

the scraping settings (min comments, per video, max videos) are separate and always configurable.

### settings (advanced)

all configurable in the UI or `.env`:

| setting | default | what it does |
|---------|---------|-------------|
| `COMMENT_THRESHOLD` | 300 | minimum comments on a video to scrape it |
| `MAX_COMMENTS_PER_VIDEO` | 50 | how many comments to grab per qualifying video |
| `MAX_VIDEOS` | 0 | auto-stop after this many scraped videos (0 = unlimited) |

## what you get

the single best app idea the AI could find (or up to 3 if multiple strong ones exist). each one includes:

- the pain point theme
- why it's worth building
- a concrete app concept
- actual comments from real people experiencing the problem

you can **export results** as markdown or JSON, and **view past sessions** in the history panel.

## how it actually works (for the nerds)

- **playwright** scrolls tiktok in a real browser
- **xhr interception** catches tiktok's internal API responses for video metadata and comments
- comments are stored in **sqlite** so nothing gets lost
- analysis is **chunked** (50 videos per batch) so it doesn't blow up on long sessions
- multiple batches get **merged** into a final top 3

## stack

python, fastapi, playwright, sqlite, and whichever AI you pick

## license

do whatever you want with it
