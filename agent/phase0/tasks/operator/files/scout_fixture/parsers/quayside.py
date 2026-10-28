"""Parser for Quayside Health Data's careers table (https://quayside.example/careers).

    <table class="careers"><tbody>
      <tr><td class="role"><a href="https://quayside.example/careers/q-311">Title</a></td>
          <td class="city">Vancouver, BC</td><td class="type">Full-time</td></tr>
"""

from html.parser import HTMLParser
from urllib.parse import urljoin

DEFAULT_BASE_URL = "https://quayside.example"
CELL_FIELDS = {"role": "title", "city": "location", "type": "employment_type"}


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self._in_table = False
        self._row = None
        self._field = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = (attrs.get("class") or "").split()
        if tag == "table" and "careers" in classes:
            self._in_table = True
        elif not self._in_table:
            return
        elif tag == "tr":
            self._row = {"title": "", "location": "", "employment_type": "", "href": None}
        elif tag == "td" and self._row is not None:
            self._field = next((CELL_FIELDS[c] for c in classes if c in CELL_FIELDS), None)
        elif tag == "a" and self._row is not None and self._field == "title":
            self._row["href"] = attrs.get("href")

    def handle_endtag(self, tag):
        if tag == "table":
            self._in_table = False
        elif tag == "td":
            self._field = None
        elif tag == "tr" and self._row is not None:
            if self._row["href"]:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._row is not None and self._field:
            self._row[self._field] += data


def parse_jobs(html: str, base_url: str = DEFAULT_BASE_URL) -> list[dict]:
    parser = _TableParser()
    parser.feed(html)
    parser.close()
    jobs = []
    for row in parser.rows:
        url = urljoin(base_url + "/", row["href"])
        jobs.append({
            "job_id": url.rstrip("/").rsplit("/", 1)[-1],
            "title": " ".join(row["title"].split()),
            "location": " ".join(row["location"].split()),
            "employment_type": " ".join(row["employment_type"].split()),
            "url": url,
        })
    return jobs
