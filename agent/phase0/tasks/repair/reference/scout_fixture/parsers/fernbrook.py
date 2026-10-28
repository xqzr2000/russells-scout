"""Reference fix (used only by `capability.py --self-test`; never shown to the agent).

Parser for Fernbrook Analytics' careers page, 2026-10 layout:

    <li class="job-card" data-job-id="4101">
      <a class="job-card__link" href="/jobs/4101-...">
        <span class="job-card__title">Title</span>
      </a>
      <p class="job-card__meta">Vancouver, BC &middot; Full-time</p>
    </li>
"""

from html.parser import HTMLParser
from urllib.parse import urljoin

DEFAULT_BASE_URL = "https://careers.fernbrook.example"


class _JobCardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = []
        self._card = None
        self._field = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "li" and "job-card" in classes:
            self._card = {"job_id": attrs.get("data-job-id"), "href": None, "title": "", "meta": ""}
        elif self._card is None:
            return
        elif tag == "a" and "job-card__link" in classes:
            self._card["href"] = attrs.get("href")
        elif tag == "span" and "job-card__title" in classes:
            self._field = "title"
        elif tag == "p" and "job-card__meta" in classes:
            self._field = "meta"

    def handle_endtag(self, tag):
        if self._card is None:
            return
        if tag in ("span", "p"):
            self._field = None
        elif tag == "li":
            self.cards.append(self._card)
            self._card = None
            self._field = None

    def handle_data(self, data):
        if self._card is not None and self._field:
            self._card[self._field] += data


def parse_jobs(html: str, base_url: str = DEFAULT_BASE_URL) -> list[dict]:
    parser = _JobCardParser()
    parser.feed(html)
    parser.close()
    jobs = []
    for card in parser.cards:
        if not card["job_id"]:
            continue  # promo cards have no job id
        location, _, employment_type = card["meta"].partition("·")
        jobs.append({
            "job_id": card["job_id"],
            "title": " ".join(card["title"].split()),
            "location": " ".join(location.split()),
            "employment_type": " ".join(employment_type.split()),
            "url": urljoin(base_url + "/", card["href"] or ""),
        })
    return jobs
