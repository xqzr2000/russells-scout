"""Thin wrappers around crawl4ai.

crawl4ai's main pieces:
  BrowserConfig     - HOW the browser is launched (headless, user agent, proxy...)
  CrawlerRunConfig  - WHAT to do on each page (wait for JS, extract, cache...)
  AsyncWebCrawler   - opens the browser once, then `arun(url, config)` per page
  CrawlResult       - what `arun` returns: html, markdown, links, extracted_content...

Keeping them in one module means the rest of the code never imports crawl4ai
directly, so it's easy to see exactly how we use it.
"""

from __future__ import annotations

import json

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlerRunConfig,
    CrawlResult,
    JsonCssExtractionStrategy,
)

from scout.extract import to_postings
from scout.models import JobPosting


def browser_config(headless: bool = True) -> BrowserConfig:
    # headless=True: no window. Required in a container; set False locally to watch it work.
    return BrowserConfig(headless=headless, verbose=True)


def run_config(**overrides) -> CrawlerRunConfig:
    defaults = dict(
        # Always fetch fresh while developing (crawl4ai can cache pages on disk).
        cache_mode=CacheMode.BYPASS,
        # Ask the site's robots.txt before crawling; skip the page if disallowed.
        check_robots_txt=True,
        # Give slow pages up to 30 s.
        page_timeout=30_000,
    )
    defaults.update(overrides)
    return CrawlerRunConfig(**defaults)


async def fetch_page(url: str, *, headless: bool = True) -> CrawlResult:
    """Load one page in Chromium and return crawl4ai's full result."""
    async with AsyncWebCrawler(config=browser_config(headless)) as crawler:
        return await crawler.arun(url=url, config=run_config())


async def extract_jobs(
    url: str,
    schema: dict,
    company: str,
    *,
    base_url: str | None = None,
    crawler: AsyncWebCrawler | None = None,
    headless: bool = True,
) -> list[JobPosting]:
    """Load a careers page and pull job postings out with a CSS schema (no LLM).

    Pass `crawler` to reuse an open browser (or a non-browser strategy in tests).
    """
    config = run_config(extraction_strategy=JsonCssExtractionStrategy(schema))

    if crawler is None:
        async with AsyncWebCrawler(config=browser_config(headless)) as own:
            result = await own.arun(url=url, config=config)
    else:
        result = await crawler.arun(url=url, config=config)

    if not result.success:
        raise RuntimeError(f"Crawl failed for {url}: {result.error_message}")

    items = json.loads(result.extracted_content or "[]")
    return to_postings(items, company=company, base_url=base_url or url)
