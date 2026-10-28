"""Parser for Fernbrook Analytics' careers page (https://careers.fernbrook.example/jobs).

parse_jobs(html, base_url) returns one dict per opening, in page order:

    {"job_id": "4101", "title": "...", "location": "...",
     "employment_type": "...", "url": "https://careers.fernbrook.example/jobs/..."}

Written for the page layout as of 2026-06:

    <div class="opening">
      <h3 class="opening-title"><a href="https://careers.fernbrook.example/jobs/123">Title</a></h3>
      <span class="opening-location">Vancouver, BC</span>
      <span class="opening-type">Full-time</span>
    </div>
"""

import re
from html.parser import HTMLParser

DEFAULT_BASE_URL = "https://careers.fernbrook.example"
JOB_ID_RE = re.compile(r"/jobs/(\d+)")


class _OpeningsParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.jobs = []
        self._job = None
        self._field = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "div" and "opening" in classes:
            self._job = {"title": "", "location": "", "employment_type": "", "url": None}
        elif self._job is None:
            return
        elif tag == "h3" and "opening-title" in classes:
            self._field = "title"
        elif tag == "a" and self._field == "title":
            self._job["url"] = attrs.get("href")
        elif tag == "span" and "opening-location" in classes:
            self._field = "location"
        elif tag == "span" and "opening-type" in classes:
            self._field = "employment_type"

    def handle_endtag(self, tag):
        if self._job is None:
            return
        if tag in ("h3", "span"):
            self._field = None
        elif tag == "div":
            job = self._job
            match = JOB_ID_RE.search(job["url"] or "")
            self.jobs.append({
                "job_id": match.group(1) if match else None,
                "title": " ".join(job["title"].split()),
                "location": " ".join(job["location"].split()),
                "employment_type": " ".join(job["employment_type"].split()),
                "url": job["url"],
            })
            self._job = None
            self._field = None

    def handle_data(self, data):
        if self._job is not None and self._field:
            self._job[self._field] += data


def parse_jobs(html: str, base_url: str = DEFAULT_BASE_URL) -> list[dict]:
    parser = _OpeningsParser()
    parser.feed(html)
    parser.close()
    return parser.jobs
