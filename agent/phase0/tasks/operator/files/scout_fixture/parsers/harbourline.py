"""Parser for Harbourline Logistics' careers page (https://harbourline.example/careers).

Written for the page layout as of 2026-06:

    <article class="posting" data-id="HL-1902">
      <h2>Title</h2>
      <span class="where">Vancouver, BC</span> <span class="kind">Full-time</span>
      <a class="apply" href="/careers/HL-1902">Apply</a>
    </article>
"""

from html.parser import HTMLParser
from urllib.parse import urljoin

DEFAULT_BASE_URL = "https://harbourline.example"
FIELDS = {"h2": "title", "where": "location", "kind": "employment_type"}


class _PostingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.postings = []
        self._post = None
        self._field = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "article" and "posting" in classes:
            self._post = {"job_id": attrs.get("data-id"), "title": "", "location": "",
                          "employment_type": "", "href": None}
        elif self._post is None:
            return
        elif tag == "h2":
            self._field = "title"
        elif tag == "span":
            self._field = next((FIELDS[c] for c in classes if c in FIELDS), None)
        elif tag == "a" and "apply" in classes:
            self._post["href"] = attrs.get("href")

    def handle_endtag(self, tag):
        if self._post is None:
            return
        if tag in ("h2", "span"):
            self._field = None
        elif tag == "article":
            self.postings.append(self._post)
            self._post = None

    def handle_data(self, data):
        if self._post is not None and self._field:
            self._post[self._field] += data


def parse_jobs(html: str, base_url: str = DEFAULT_BASE_URL) -> list[dict]:
    parser = _PostingParser()
    parser.feed(html)
    parser.close()
    return [{
        "job_id": p["job_id"],
        "title": " ".join(p["title"].split()),
        "location": " ".join(p["location"].split()),
        "employment_type": " ".join(p["employment_type"].split()),
        "url": urljoin(base_url + "/", p["href"] or ""),
    } for p in parser.postings]
