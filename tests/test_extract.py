"""Offline tests: no network, no browser.

They use crawl4ai's AsyncHTTPCrawlerStrategy (a plain HTTP/file fetcher) instead of
Chromium, so they run in seconds. Because there's no browser, JavaScript doesn't
run, which is exactly what test_http_fetch_misses_js_jobs demonstrates.
"""

import asyncio
from pathlib import Path

from crawl4ai import AsyncWebCrawler
from crawl4ai.async_crawler_strategy import AsyncHTTPCrawlerStrategy

from scout.crawl import extract_jobs
from scout.extract import SAMPLE_CAREERS_SCHEMA, to_postings
from scout.models import JobPosting

SAMPLE = Path(__file__).resolve().parents[1] / "examples" / "sample_careers.html"
BASE = "https://careers.example.com/"


def _extract_without_browser():
    async def run():
        async with AsyncWebCrawler(crawler_strategy=AsyncHTTPCrawlerStrategy()) as crawler:
            return await extract_jobs(
                SAMPLE.as_uri(),
                SAMPLE_CAREERS_SCHEMA,
                "Example Corp",
                base_url=BASE,
                crawler=crawler,
            )

    return asyncio.run(run())


def test_http_fetch_extracts_static_jobs():
    postings = _extract_without_browser()
    titles = [p.title for p in postings]
    assert titles == ["Data Scientist", "Machine Learning Engineer", "AI Agent Developer"]


def test_http_fetch_misses_js_jobs():
    # "Data Engineer" is added by JavaScript, so only a real browser finds it.
    assert "Data Engineer" not in [p.title for p in _extract_without_browser()]


def test_relative_links_become_absolute():
    by_title = {p.title: p for p in _extract_without_browser()}
    assert by_title["Data Scientist"].url == "https://careers.example.com/jobs/101-data-scientist"
    # Already-absolute links are left alone.
    assert by_title["AI Agent Developer"].url == "https://jobs.example-ats.com/example-corp/103"


def test_to_postings_skips_incomplete_items():
    items = [{"title": "Analyst", "url": "/a"}, {"title": "", "url": "/b"}, {"title": "No link"}]
    assert [p.title for p in to_postings(items, "X", BASE)] == ["Analyst"]


def test_job_id_is_stable_per_url():
    a = JobPosting(company="X", title="A", url="https://x.com/jobs/1")
    b = JobPosting(company="X", title="A (renamed)", url="https://X.com/jobs/1 ")
    assert a.job_id == b.job_id
