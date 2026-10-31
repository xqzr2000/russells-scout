"""WorkBC job board search client (docs/phase1-plan.md §1 and §3 steps 1–2, task 1.3).

One sweep = every search term, every page, newest first, titles only, all of B.C.

What counts as success, per term (this feeds removal safety later: a posting is only
counted as "missed" after a sweep in which every term was complete):

  ok + complete   all pages came back and we hold at least `count` distinct postings
  ok, empty       WorkBC says 0 postings for the term (normal)
  ok, incomplete  stopped at max_pages_per_term before reaching `count`
  transient       timeouts, 429 or 5xx, still failing after retries
  restricted      401/403: we're being refused
  structural      the response isn't what we expect (not JSON, no result list,
                  jobs without JobId/Title, or a 400 because the API changed)

A failing term never stops the others. The raw response of every page can be saved for
debugging. The HTTP call is injectable so tests run on synthetic responses.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_API = "https://workbc-jb.a55eb5-prod.stratus.cloud.gov.bc.ca/api/Search/JobSearch"
USER_AGENT = "russells-scout/0.1 (personal job search; github.com/xqzr2000/russells-scout)"
TIMEOUT_SECONDS = 30
RETRIES = 2  # after the first try; waits RETRY_WAIT * attempt between tries
RETRY_WAIT = 5.0

# Search body: SortOrder 1 = newest first; SearchInField "title" = titles only.
SORT_NEWEST_FIRST = 1


class HttpError(Exception):
    def __init__(self, status: int, body: bytes = b""):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body


class TransportError(Exception):
    """Network-level failure (timeout, DNS, connection reset)."""


Post = Callable[[str, dict], bytes]


def http_post(url: str, body: dict) -> bytes:
    """POST JSON, return the raw response body. Raises HttpError or TransportError."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read()[:2000]) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise TransportError(str(getattr(e, "reason", e))) from None


def search_body(term: str, page: int, page_size: int) -> dict:
    # Only fields WorkBC knows: unknown fields are rejected with 400.
    return {
        "Page": page,
        "PageSize": page_size,
        "Keyword": term,
        "SearchInField": "title",
        "SortOrder": SORT_NEWEST_FIRST,
    }


@dataclass
class TermResult:
    term: str
    outcome: str = "ok"  # ok | empty | transient | restricted | structural
    count: int | None = None  # WorkBC's total for the term (last page seen)
    pages: int = 0
    jobs: dict[str, dict] = field(default_factory=dict)  # JobId -> raw job
    complete: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome in ("ok", "empty")


@dataclass
class SweepResult:
    terms: list[TermResult]
    jobs: dict[str, dict]  # JobId -> raw job (one copy per posting)
    matched_terms: dict[str, list[str]]  # JobId -> terms that found it, in config order
    started_at: float
    finished_at: float
    raw_dir: Path | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Every term succeeded and returned everything: safe to count missed postings."""
        return all(t.ok and t.complete for t in self.terms) and not self.suspicious_empty

    @property
    def suspicious_empty(self) -> bool:
        """Every term empty at once: more likely a broken API than a quiet day (plan §11)."""
        return bool(self.terms) and all(t.outcome == "empty" for t in self.terms)


def _slug(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", term.lower()).strip("_") or "term"


def _parse_page(raw: bytes) -> tuple[int, list[dict]]:
    """(count, jobs) from one response page; ValueError if the shape is wrong."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        start = raw[:60].decode("utf-8", "replace").strip()
        raise ValueError(f"response is not JSON (starts {start!r})") from None
    if not isinstance(data, dict):
        raise ValueError("response is not a JSON object")
    # Outer keys are lowercase in production; accept PascalCase too.
    result = data.get("result", data.get("Result"))
    count = data.get("count", data.get("Count"))
    if not isinstance(result, list):
        raise ValueError("response has no result list")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise ValueError(f"response count is not a number: {count!r}")
    for i, job in enumerate(result):
        if not isinstance(job, dict) or not job.get("JobId") or not job.get("Title"):
            raise ValueError(f"job {i} has no JobId or Title")
    return count, result


class _Saver:
    def __init__(self, folder: Path | None, warn: Callable[[str], None]):
        self.folder = folder
        self.warn = warn
        if folder is not None:
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                self.warn(f"not saving raw responses: can't create {folder}: {e.strerror}")
                self.folder = None

    def save(self, name: str, raw: bytes) -> None:
        if self.folder is None:
            return
        try:
            (self.folder / name).write_bytes(raw)
        except OSError as e:
            self.warn(f"not saving raw responses: can't write in {self.folder}: {e.strerror}")
            self.folder = None


def fetch_term(
    term: str,
    *,
    page_size: int,
    max_pages: int,
    delay: float,
    post: Post = http_post,
    api: str = DEFAULT_API,
    sleep: Callable[[float], None] = time.sleep,
    saver: _Saver | None = None,
    first_request: list[bool] | None = None,
) -> TermResult:
    """Fetch every page of one term. Never raises for HTTP or data problems."""
    res = TermResult(term)
    first_request = first_request if first_request is not None else [True]

    for page in range(1, max_pages + 1):
        raw = None
        for attempt in range(RETRIES + 1):
            if not first_request[0]:
                sleep(delay)
            first_request[0] = False
            try:
                raw = post(api, search_body(term, page, page_size))
                break
            except HttpError as e:
                if e.status in (401, 403):
                    res.outcome, res.error = "restricted", f"page {page}: HTTP {e.status}"
                    return res
                if e.status == 429 or e.status >= 500:
                    res.error = f"page {page}: HTTP {e.status}"
                else:  # 400 and other 4xx: our request no longer fits the API
                    detail = e.body[:200].decode("utf-8", "replace").strip()
                    res.outcome = "structural"
                    res.error = f"page {page}: HTTP {e.status} {detail}".strip()
                    return res
            except TransportError as e:
                res.error = f"page {page}: {e}"
            if attempt < RETRIES:
                sleep(RETRY_WAIT * (attempt + 1))
        if raw is None:
            res.outcome = "transient"
            return res
        res.error = None

        if saver:
            saver.save(f"{_slug(term)}-p{page}.json", raw)
        try:
            count, jobs = _parse_page(raw)
        except ValueError as e:
            res.outcome, res.error = "structural", f"page {page}: {e}"
            return res

        res.pages = page
        res.count = count
        for job in jobs:
            res.jobs.setdefault(str(job["JobId"]), job)

        if count == 0:
            res.outcome, res.complete = "empty", True
            return res
        if len(res.jobs) >= count:
            res.complete = True
            return res
        if not jobs:  # ran out of pages before reaching count (postings closed mid-sweep)
            res.error = f"WorkBC reported {count} but returned {len(res.jobs)}"
            return res

    res.error = f"stopped at {max_pages} pages with {len(res.jobs)} of {res.count}"
    return res


def sweep(
    terms: list[str] | tuple[str, ...],
    *,
    page_size: int,
    max_pages: int,
    delay: float,
    post: Post = http_post,
    api: str = DEFAULT_API,
    sleep: Callable[[float], None] = time.sleep,
    raw_dir: Path | None = None,
    clock: Callable[[], float] = time.time,
) -> SweepResult:
    warnings: list[str] = []
    saver = _Saver(raw_dir, warnings.append) if raw_dir else None
    first_request = [True]
    started = clock()
    results: list[TermResult] = []
    jobs: dict[str, dict] = {}
    matched: dict[str, list[str]] = {}
    for term in terms:
        r = fetch_term(
            term,
            page_size=page_size,
            max_pages=max_pages,
            delay=delay,
            post=post,
            api=api,
            sleep=sleep,
            saver=saver,
            first_request=first_request,
        )
        results.append(r)
        for job_id, job in r.jobs.items():
            jobs.setdefault(job_id, job)
            matched.setdefault(job_id, []).append(term)
    return SweepResult(
        terms=results,
        jobs=jobs,
        matched_terms=matched,
        started_at=started,
        finished_at=clock(),
        raw_dir=saver.folder if saver else None,
        warnings=warnings,
    )


def jump_url(job: dict) -> str | None:
    """The link to the employer's own site or ATS (None for Job Bank postings)."""
    sources = (job.get("ExternalSource") or {}).get("Source") or []
    for s in sources:
        if isinstance(s, dict) and s.get("Url"):
            return s["Url"]
    return None
