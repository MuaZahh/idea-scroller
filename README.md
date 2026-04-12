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

### modes

pick a mode in the UI dropdown:

| mode | min comments | per video | max videos | time |
|------|-------------|-----------|------------|------|
| **Quick** | 500+ | 20 | 15 | ~2 min |
| **Balanced** | 300+ | 50 | 30 | ~5 min |
| **Thorough** | 100+ | 50 | unlimited | ~15+ min |

**quick** is for a fast scan — only grabs the most viral videos. **balanced** is the default, good mix of speed and coverage. **thorough** scrolls until you stop it, catches everything.

you can also tweak each setting manually after picking a mode.

### heads up

the analysis is very critical. it filters out ideas that:
- a normal person would already think to use ChatGPT for
- already have a well-known app
- don't have enough people talking about them (300+ comments minimum)

**this means sometimes it might return nothing**, even after a long session. that's by design — no result is better than a bad result. if you're getting empty results, try **thorough** mode and let it scroll longer. more data = better chance of finding something real.

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
