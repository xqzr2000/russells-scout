"""Command line entry point: `scout <command>`.

  scout smoke [URL]   Load a real page in Chromium, save markdown + metadata as JSON.
  scout sample        Extract jobs from examples/sample_careers.html, save as JSON.

Add --show-browser to either command to run Chromium with a visible window
(only works where there's a display, i.e. not in Codespaces).
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

from scout.crawl import extract_jobs, fetch_page
from scout.extract import SAMPLE_CAREERS_SCHEMA
from scout.writers import write_json, write_postings

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / "output"
SAMPLE_PAGE = REPO_ROOT / "examples" / "sample_careers.html"

# A JavaScript-rendered practice site built for scraping exercises: its quotes only
# appear after JS runs, so a successful result proves the browser is working.
DEFAULT_SMOKE_URL = "https://quotes.toscrape.com/js/"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


async def smoke(url: str, headless: bool) -> int:
    result = await fetch_page(url, headless=headless)
    if not result.success:
        print(f"FAILED: {result.error_message}")
        return 1

    markdown = str(result.markdown or "")
    record = {
        "url": result.url,
        "status_code": result.status_code,
        "title": (result.metadata or {}).get("title"),
        "markdown_chars": len(markdown),
        "internal_links": len(result.links.get("internal", [])),
        "external_links": len(result.links.get("external", [])),
        "markdown": markdown,
    }
    path = write_json(record, OUTPUT_DIR / f"smoke-{_stamp()}.json")
    print(f"OK: {record['title']!r}, {len(markdown)} chars of markdown -> {path}")
    return 0


async def sample(headless: bool) -> int:
    url = SAMPLE_PAGE.as_uri()  # file:///.../examples/sample_careers.html
    postings = await extract_jobs(
        url,
        SAMPLE_CAREERS_SCHEMA,
        company="Example Corp",
        # Pretend the page lives on a real site so relative links resolve sensibly.
        base_url="https://careers.example.com/",
        headless=headless,
    )
    path = write_postings(postings, OUTPUT_DIR / f"sample-jobs-{_stamp()}.json")
    for p in postings:
        print(f"  - {p.title} ({p.location}) {p.url}")
    print(f"OK: {len(postings)} postings -> {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scout")
    parser.add_argument("--show-browser", action="store_true", help="run Chromium with a window")
    sub = parser.add_subparsers(dest="command", required=True)

    p_smoke = sub.add_parser("smoke", help="crawl one real page and save it as JSON")
    p_smoke.add_argument("url", nargs="?", default=DEFAULT_SMOKE_URL)

    sub.add_parser("sample", help="extract jobs from the bundled sample careers page")

    args = parser.parse_args(argv)
    headless = not args.show_browser

    if args.command == "smoke":
        return asyncio.run(smoke(args.url, headless))
    if args.command == "sample":
        return asyncio.run(sample(headless))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
