"""Turn crawl4ai's raw extraction output into JobPosting records."""

from __future__ import annotations

from urllib.parse import urljoin

from scout.models import JobPosting

# A CSS extraction schema for examples/sample_careers.html.
#
# How JsonCssExtractionStrategy reads it:
#   baseSelector - each element matching this is ONE record (one job card)
#   fields       - CSS selectors searched *inside* that element
#     type "text"      -> the element's visible text
#     type "attribute" -> the value of `attribute` (here the link's href)
#
# Every real careers page will need its own schema like this one (stage 2).
SAMPLE_CAREERS_SCHEMA = {
    "name": "Sample careers page",
    "baseSelector": "li.job-card",
    "fields": [
        {"name": "title", "selector": "a.job-title", "type": "text"},
        {"name": "url", "selector": "a.job-title", "type": "attribute", "attribute": "href"},
        {"name": "location", "selector": ".job-location", "type": "text"},
    ],
}


def to_postings(items: list[dict], company: str, base_url: str) -> list[JobPosting]:
    """Clean raw dicts into JobPostings; skip any without a title or link."""
    postings = []
    for item in items:
        title = (item.get("title") or "").strip()
        href = (item.get("url") or "").strip()
        if not title or not href:
            continue
        postings.append(
            JobPosting(
                company=company,
                title=title,
                # Pages often use relative links like /jobs/42; make them absolute.
                url=urljoin(base_url, href),
                location=(item.get("location") or "").strip() or None,
                source_url=base_url,
            )
        )
    return postings
