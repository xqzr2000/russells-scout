"""Public job-board APIs: Ashby, Greenhouse, Lever (docs/phase1-plan.md §3 step 7, task 1.8).

One GET per board returns every open posting on it; nothing needs a key.

  Ashby       GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true
  Greenhouse  GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
  Lever       GET https://api.lever.co/v0/postings/{board}?mode=json  (EU host if 404)

Each board is turned into a list of `BoardPosting` with the same fields whatever the ATS.
Workplace comes from the ATS's own field (Ashby `workplaceType`, Lever `workplaceType`),
never from Ashby's `isRemote`, which is also true for hybrid jobs. Greenhouse's board list
has no workplace or pay fields.

Greenhouse links on a company's own site carry only `?gh_jid=<id>`. `find_greenhouse_board`
tries a few board names made from the employer's name and keeps the one where that exact
job id exists, so a guess can't attach the wrong employer's board.
"""

from __future__ import annotations

import html
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote, urlparse

from scout.sources.workbc import USER_AGENT, HttpError, TransportError

TIMEOUT_SECONDS = 30
RETRIES = 2
RETRY_WAIT = 5.0
MAX_DESCRIPTION = 20_000

ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
GREENHOUSE_JOB = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"
LEVER = "https://api.lever.co/v0/postings/{board}?mode=json"
LEVER_EU = "https://api.eu.lever.co/v0/postings/{board}?mode=json"
SUPPORTED = ("ashby", "greenhouse", "lever")

Get = Callable[[str], bytes]


def http_get(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read()[:2000]) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise TransportError(str(getattr(e, "reason", e))) from None


@dataclass(frozen=True)
class BoardPosting:
    ats: str
    board: str
    posting_id: str
    title: str
    url: str | None
    location: str | None = None  # as the board writes it, e.g. "Vancouver, BC"
    city: str | None = None
    country: str | None = None
    workplace: str = "unknown"  # remote | hybrid | onsite | unknown
    description: str | None = None
    pay_min: float | None = None
    pay_max: float | None = None
    pay_currency: str | None = None
    pay_period: str | None = None  # year | month | week | day | hour
    published_at: str | None = None


@dataclass
class BoardResult:
    ats: str
    board: str
    outcome: str = "ok"  # ok | empty | transient | restricted | structural
    postings: list[BoardPosting] = field(default_factory=list)
    error: str | None = None
    url: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome in ("ok", "empty")


def text_from_html(fragment: str | None) -> str | None:
    """Plain text from an HTML fragment (Greenhouse sends it entity-encoded)."""
    if not fragment:
        return None
    s = html.unescape(html.unescape(fragment))
    s = re.sub(r"(?i)<\s*(br|/p|/li|/h[1-6]|/div|/tr)\s*/?>", "\n", s)  # block ends: new line
    s = re.sub(r"<[^>]+>", "", s)  # inline tags vanish
    s = html.unescape(s)
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in s.splitlines()]
    s = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return s[:MAX_DESCRIPTION] or None


def _clip(text: str | None) -> str | None:
    text = (text or "").strip()
    return text[:MAX_DESCRIPTION] or None


def _workplace(value: object) -> str:
    v = str(value or "").replace("-", "").replace("_", "").replace(" ", "").casefold()
    return {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "inoffice": "onsite"}.get(
        v, "unknown"
    )


_PERIODS = {"year": "year", "month": "month", "week": "week", "day": "day", "hour": "hour"}


def _period(value: object) -> str | None:
    v = str(value or "").casefold()
    for key, period in _PERIODS.items():
        if key in v:
            return period
    return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _city(location: str | None) -> str | None:
    if not location:
        return None
    first = re.split(r"[,;(]|\s-\s", location)[0].strip()
    return first or None


def parse_ashby(board: str, data: object) -> list[BoardPosting]:
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("no jobs list")
    out = []
    for i, j in enumerate(data["jobs"]):
        if not isinstance(j, dict) or not j.get("id") or not j.get("title"):
            raise ValueError(f"job {i} has no id or title")
        if j.get("isListed") is False:
            continue
        postal = ((j.get("address") or {}).get("postalAddress")) or {}
        pay = next(
            (
                c
                for c in ((j.get("compensation") or {}).get("summaryComponents") or [])
                if isinstance(c, dict) and c.get("compensationType") == "Salary"
            ),
            {},
        )
        out.append(
            BoardPosting(
                ats="ashby",
                board=board,
                posting_id=str(j["id"]),
                title=" ".join(str(j["title"]).split()),
                url=j.get("jobUrl"),
                location=j.get("location"),
                city=postal.get("addressLocality") or _city(j.get("location")),
                country=postal.get("addressCountry"),
                workplace=_workplace(j.get("workplaceType")),
                description=_clip(j.get("descriptionPlain"))
                or text_from_html(j.get("descriptionHtml")),
                pay_min=_number(pay.get("minValue")),
                pay_max=_number(pay.get("maxValue")),
                pay_currency=pay.get("currencyCode"),
                pay_period=_period(pay.get("interval")),
                published_at=j.get("publishedAt"),
            )
        )
    return out


def parse_greenhouse(board: str, data: object) -> list[BoardPosting]:
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        raise ValueError("no jobs list")
    out = []
    for i, j in enumerate(data["jobs"]):
        if not isinstance(j, dict) or not j.get("id") or not j.get("title"):
            raise ValueError(f"job {i} has no id or title")
        location = (j.get("location") or {}).get("name")
        out.append(
            BoardPosting(
                ats="greenhouse",
                board=board,
                posting_id=str(j["id"]),
                title=" ".join(str(j["title"]).split()),
                url=j.get("absolute_url"),
                location=location,
                city=_city(location),
                workplace="remote" if re.search(r"(?i)\bremote\b", location or "") else "unknown",
                description=text_from_html(j.get("content")),
                published_at=j.get("first_published") or j.get("updated_at"),
            )
        )
    return out


def parse_lever(board: str, data: object) -> list[BoardPosting]:
    if not isinstance(data, list):
        raise ValueError("not a list of postings")
    out = []
    for i, j in enumerate(data):
        if not isinstance(j, dict) or not j.get("id") or not j.get("text"):
            raise ValueError(f"posting {i} has no id or text")
        cats = j.get("categories") or {}
        parts = [j.get("descriptionPlain") or ""]
        for item in j.get("lists") or []:
            if isinstance(item, dict):
                parts.append(
                    f"{item.get('text') or ''}\n{text_from_html(item.get('content')) or ''}"
                )
        parts.append(j.get("additionalPlain") or "")
        salary = j.get("salaryRange") or {}
        created = j.get("createdAt")
        published = (
            datetime.fromtimestamp(created / 1000, UTC).isoformat()
            if isinstance(created, (int, float)) and not isinstance(created, bool)
            else None
        )
        out.append(
            BoardPosting(
                ats="lever",
                board=board,
                posting_id=str(j["id"]),
                title=" ".join(str(j["text"]).split()),
                url=j.get("hostedUrl"),
                location=cats.get("location"),
                city=_city(cats.get("location")),
                country=j.get("country"),
                workplace=_workplace(j.get("workplaceType")),
                description=_clip("\n\n".join(p for p in parts if p.strip())),
                pay_min=_number(salary.get("min")),
                pay_max=_number(salary.get("max")),
                pay_currency=salary.get("currency"),
                pay_period=_period(salary.get("interval")),
                published_at=published,
            )
        )
    return out


PARSERS = {"ashby": parse_ashby, "greenhouse": parse_greenhouse, "lever": parse_lever}
URLS = {"ashby": [ASHBY], "greenhouse": [GREENHOUSE], "lever": [LEVER, LEVER_EU]}


def _get_with_retries(url: str, get: Get, sleep) -> tuple[bytes | None, str | None, int | None]:
    """(body, error, http_status)"""
    error, status = None, None
    for attempt in range(RETRIES + 1):
        try:
            return get(url), None, None
        except HttpError as e:
            status, error = e.status, f"HTTP {e.status}"
            if e.status not in (429,) and e.status < 500:
                return None, error, status
        except TransportError as e:
            status, error = None, str(e)
        if attempt < RETRIES:
            sleep(RETRY_WAIT * (attempt + 1))
    return None, error, status


def fetch_board(ats: str, board: str, *, get: Get = http_get, sleep=time.sleep) -> BoardResult:
    """Fetch and parse one board. Never raises for HTTP or data problems."""
    if ats not in SUPPORTED:
        return BoardResult(ats, board, "structural", error=f"unsupported ATS {ats!r}")
    res = BoardResult(ats, board)
    raw = None
    for template in URLS[ats]:
        res.url = template.format(board=quote(board, safe=""))
        raw, error, status = _get_with_retries(res.url, get, sleep)
        if raw is not None:
            break
        if status == 404:  # try the next host (Lever EU) or report the board as gone
            res.outcome, res.error = "structural", "board not found (HTTP 404)"
            continue
        res.outcome = (
            "restricted"
            if status in (401, 403)
            else ("structural" if status and status < 500 and status != 429 else "transient")
        )
        res.error = error
        return res
    if raw is None:
        return res
    try:
        res.postings = PARSERS[ats](board, json.loads(raw))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as e:
        res.outcome, res.error, res.postings = "structural", f"unexpected response: {e}", []
        return res
    res.outcome, res.error = ("ok" if res.postings else "empty"), None
    return res


def greenhouse_board_candidates(
    employer_name: str | None, employer_norm: str | None, link_host: str | None
) -> list[str]:
    """Likely board names for an employer, most likely first (at most 4)."""
    out: list[str] = []

    def add(c: str | None) -> None:
        c = (c or "").strip("-")
        if c and c not in out:
            out.append(c)

    words = (employer_norm or "").split()
    if words:
        add("".join(words))
        add("-".join(words))
        add(words[0])
    if link_host:
        parts = [p for p in link_host.split(".") if p not in ("www", "careers", "jobs")]
        if len(parts) >= 2:
            add(parts[-2])
    return out[:4]


def find_greenhouse_board(
    gh_jid: str, candidates: list[str], *, get: Get = http_get, sleep=time.sleep, delay=1.0
) -> str | None:
    """The first candidate board on which job `gh_jid` exists, or None."""
    for n, board in enumerate(candidates):
        if n:
            sleep(delay)
        url = GREENHOUSE_JOB.format(board=quote(board, safe=""), job_id=quote(gh_jid, safe=""))
        raw, _error, _status = _get_with_retries(url, get, sleep)
        if raw is None:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(data, dict) and str(data.get("id")) == str(gh_jid):
            return board
    return None


def host_of(url: str | None) -> str | None:
    return urlparse(url).netloc.lower() if url else None
