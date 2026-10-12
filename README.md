# russells-scout

Project Scout, stage 1: a [crawl4ai](https://github.com/unclecode/crawl4ai) (v0.9.4) job scout
that runs in a devcontainer, so it behaves the same in GitHub Codespaces, local VS Code, and
(later) a Docker container on the Ubuntu box.

> This project uses Crawl4AI (<https://github.com/unclecode/crawl4ai>) for web data extraction.

## Roadmap

| Stage | What | Status |
|---|---|---|
| 1 | Repo + devcontainer, smoke crawl, offline extraction demo, JSON output | **this commit** |
| 2 | `targets.yaml`, one extractor per page type (Greenhouse / Lever / Ashby APIs, CSS schemas), rate limits, dedup | next |
| 3 | Postgres writer + schema, Postgres service in the devcontainer | |
| 4 | `prod` Docker stage + compose on Ubuntu (host Postgres, scheduling) | |

## Getting started

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

## What's in the repo

```
.devcontainer/devcontainer.json  how VS Code/Codespaces builds and opens the container
Dockerfile                       base stage (Python 3.12 + Chromium + crawl4ai) and dev stage
requirements.txt                 pinned runtime versions (crawl4ai 0.9.4, playwright 1.63.0)
requirements-dev.txt             pytest, ruff
src/scout/
  models.py    JobPosting: the record we store (and later the Postgres row)
  crawl.py     the ONLY module that talks to crawl4ai
  extract.py   CSS schema for the sample page + raw dict -> JobPosting cleanup
  writers.py   JSON output (stage 3 adds Postgres)
  cli.py       `scout smoke` / `scout sample`
examples/sample_careers.html     fake careers page, 3 static jobs + 1 added by JavaScript
tests/test_extract.py            offline tests (no browser, no network)
.vscode/launch.json              F5 debug configs that step into crawl4ai's source
```

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
