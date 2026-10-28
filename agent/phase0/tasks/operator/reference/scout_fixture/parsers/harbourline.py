"""Reference fix (used only by `capability.py --self-test`; never shown to the agent).

Harbourline's careers page (2026-10 layout) is a JavaScript app; the postings are
only in the page's JSON-LD (<script type="application/ld+json">) as schema.org
JobPosting items, usually inside an "@graph" list.
"""

import json
from html.parser import HTMLParser
from urllib.parse import urljoin

DEFAULT_BASE_URL = "https://harbourline.example"
EMPLOYMENT_TYPES = {
    "FULL_TIME": "Full-time",
    "PART_TIME": "Part-time",
    "CONTRACTOR": "Contract",
    "TEMPORARY": "Temporary",
    "INTERN": "Internship",
}


class _JsonLdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.blocks = []
        self._buf = None

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "script" and self._buf is not None:
            self.blocks.append("".join(self._buf))
            self._buf = None

    def handle_data(self, data):
        if self._buf is not None:
            self._buf.append(data)


def _items(node):
    if isinstance(node, list):
        for item in node:
            yield from _items(item)
    elif isinstance(node, dict):
        if "@graph" in node:
            yield from _items(node["@graph"])
        else:
            yield node


def parse_jobs(html: str, base_url: str = DEFAULT_BASE_URL) -> list[dict]:
    parser = _JsonLdParser()
    parser.feed(html)
    parser.close()
    jobs = []
    for block in parser.blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        for item in _items(data):
            if item.get("@type") != "JobPosting":
                continue
            ident = item.get("identifier")
            job_id = ident.get("value") if isinstance(ident, dict) else ident
            address = ((item.get("jobLocation") or {}).get("address")) or {}
            location = ", ".join(p for p in (address.get("addressLocality"), address.get("addressRegion")) if p)
            raw_type = item.get("employmentType") or ""
            jobs.append({
                "job_id": job_id,
                "title": " ".join((item.get("title") or "").split()),
                "location": location,
                "employment_type": EMPLOYMENT_TYPES.get(raw_type, raw_type.replace("_", " ").title()),
                "url": urljoin(base_url + "/", item.get("url") or ""),
            })
    return jobs
