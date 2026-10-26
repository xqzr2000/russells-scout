# Russell's Scout

Project Scout finds data science, data analysis and ML jobs in Vancouver and stores them in
PostgreSQL. Scouting scripts run on timers with zero LLM tokens; an agent (Claude Code on the
DeepSeek API, in a hardened container) builds and repairs those scripts.
Full design: [docs/architecture-plan.md](docs/architecture-plan.md) (v3.0).

> This project uses Crawl4AI (<https://github.com/unclecode/crawl4ai>) for web data extraction.

## Roadmap

| Phase | What | Status |
|---|---|---|
| 0 | Claude Code on DeepSeek in a hardened container on the box; locked rules; smoke tests | **setup + smoke tests in this commit** ([box setup](docs/box-setup.md)); capability tests next |
| 1 | Scouting slice: Postgres, ATS adapters, trust scoring, dedupe (zero tokens) | |
| 2 | Scout runtime, daily digest, WSL2 sync, backups | |
| 3 | The agent takes over (sessions on a timer, `CLAUDE.md`, local git repo) | |
| 4–5 | Discovery and long-run hardening | |

The crawl4ai scaffold below (devcontainer, `src/scout`, sample extraction) is the starting
point for Phase 1.

## Repository layout

| Path | What |
|---|---|
| `compose.yaml` | Services on the box (today: the agent) |
| `agent/Dockerfile` | Agent image: Node 22, Claude Code 2.1.310 (pinned), Python, git |
| `agent/managed-settings/` | Locked permission rules and guard hook (installed read-only on the host) |
| `agent/run-session.sh` | Start one headless session with a turn limit and timeout |
| `agent/phase0/` | Smoke tests and guard-rule tests |
| `docs/` | Architecture plan, box setup, Phase 0 results |
| `src/scout/`, `tests/`, `Dockerfile`, `.devcontainer/` | crawl4ai scaffold (stage 1) |

## Agent on the Ubuntu box

Step by step from WSL2: [docs/box-setup.md](docs/box-setup.md). In short:

```bash
docker compose --profile agent build agent
agent/phase0/smoke.sh --offline   # no API calls
agent/phase0/smoke.sh             # a few cents of DeepSeek
agent/run-session.sh "your task" 20 900
```

## crawl4ai scaffold: getting started

### Option A: GitHub Codespaces
1. Push this repo to GitHub.
2. **Code → Codespaces → Create codespace on main.** The first build takes a few minutes
   (it installs Chromium and its system libraries); later starts reuse the image.
3. In the terminal:
   ```bash
   pytest                 # offline tests, a few seconds
   scout sample           # extract jobs from examples/sample_careers.html
   scout smoke            # crawl a real JS-rendered page
   ```
   Results land in `output/` as JSON.

### Option B: local VS Code (e.g. the Ubuntu box)
Needs Docker (Engine or Desktop) and the **Dev Containers** extension.
Open the folder → **Reopen in Container**. Same commands as above.
Locally you can also run `scout --show-browser smoke` to watch Chromium work (needs a display).


## How a crawl works (read `crawl.py` alongside this)

1. **`BrowserConfig`** sets how Chromium launches (headless, user agent, proxy).
2. **`AsyncWebCrawler`** starts the browser once; reuse it for many pages.
3. **`CrawlerRunConfig`** sets what happens per page: cache mode, `check_robots_txt=True`,
   timeouts, and an **extraction strategy**.
4. **`crawler.arun(url, config)`** loads the page, runs its JavaScript, then returns a
   **`CrawlResult`**: `html`, `markdown`, `links`, `metadata`, and `extracted_content`.
5. **`JsonCssExtractionStrategy(schema)`** turns the page into a JSON list using CSS
   selectors: no LLM involved. See `SAMPLE_CAREERS_SCHEMA` in `extract.py`.

Things worth noticing while you study it:
- **Browser vs plain HTTP.** `scout sample` (Chromium) finds 4 jobs; the tests use
  `AsyncHTTPCrawlerStrategy` (no browser) and find only 3, because the 4th is added by JavaScript.
  That's the main reason a careers page needs a real browser.
- **Anti-bot heuristic.** crawl4ai marks near-empty pages (under ~100 bytes) as "blocked by
  anti-bot protection" even when they returned HTTP 200. If a tiny test page fails, that's why.
- **Robots.txt.** `check_robots_txt=True` makes crawl4ai skip pages the site disallows.
  Local `file://` and `raw:` pages have no domain, so they're always allowed.
- **Shared memory.** `--shm-size=1g` in `devcontainer.json` stops Chromium crashing in Docker.

## Debugging into crawl4ai

Open **Run and Debug**, pick a "Scout:" config, set a breakpoint in `src/scout/crawl.py`, and
press F5. `justMyCode` is off, so **Step Into** on `crawler.arun(...)` takes you into crawl4ai
itself (`async_webcrawler.py` is the place to start).

## Updating crawl4ai

Change the pins in `requirements.txt`, then **Dev Containers: Rebuild Container**
(Codespaces: *Rebuild container* from the command palette).
