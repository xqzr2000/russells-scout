"""Public job-board APIs: Ashby, Greenhouse, Lever, Workday (docs/phase1-plan.md §3 step 7).

One GET per board returns every open posting on it; nothing needs a key.

  Ashby       GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true
  Greenhouse  GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true
  Lever       GET https://api.lever.co/v0/postings/{board}?mode=json  (EU host if 404)

Workday has no public board API. Its career site calls an unofficial JSON API; a board is
`tenant/site` and its host (e.g. `fabrikam.wd3.myworkdayjobs.com`) comes from the posting's
link, not the board name:

  search  POST https://{host}/wday/cxs/{tenant}/{site}/jobs
  details GET  https://{host}/wday/cxs/{tenant}/{site}{externalPath}

The search is run once per configured term (all pages of each, 20 per page) so the result
is the same set every run. Details are only fetched for postings Scout would keep: a WorkBC
posting on the board, or a title that matches a term in Metro Vancouver or remote.

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
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from urllib.parse import quote, urlparse

from scout.pipeline import classify
from scout.sources.workbc import USER_AGENT, HttpError, TransportError

TIMEOUT_SECONDS = 30
RETRIES = 2
RETRY_WAIT = 5.0
MAX_DESCRIPTION = 20_000
WORKDAY_PAGE_SIZE = 20  # the API's maximum

ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
GREENHOUSE_JOB = "https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}"
LEVER = "https://api.lever.co/v0/postings/{board}?mode=json"
LEVER_EU = "https://api.eu.lever.co/v0/postings/{board}?mode=json"
# Only real Workday career-site hosts are ever contacted: the host comes from data.
WORKDAY_HOST = re.compile(r"[a-z0-9-]+\.wd[0-9]+\.myworkdayjobs\.com")
WORKDAY_HOST_SQL = r"^[a-z0-9-]+\.wd[0-9]+\.myworkdayjobs\.com$"
WORKDAY = "https://{host}/wday/cxs/{tenant}/{site}/jobs"
WORKDAY_JOB = "https://{host}/wday/cxs/{tenant}/{site}{path}"
SUPPORTED = ("ashby", "greenhouse", "lever", "workday")

Get = Callable[[str], bytes]
Post = Callable[[str, bytes], bytes]


def _open(req: urllib.request.Request) -> bytes:
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, e.read()[:2000]) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise TransportError(str(getattr(e, "reason", e))) from None


def http_get(url: str) -> bytes:
    return _open(
        urllib.request.Request(
            url, headers={"Accept": "application/json", "User-Agent": USER_AGENT}
        )
    )


def http_post(url: str, body: bytes) -> bytes:
    return _open(
        urllib.request.Request(
            url,
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="POST",
        )
    )


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


# --------------------------------------------------------------------------- Workday

_COUNTRIES = {
    "canada", "united states", "united states of america", "usa", "us", "united kingdom",
    "uk", "ireland", "australia", "new zealand", "india", "germany", "france", "spain",
    "netherlands", "singapore", "japan", "china", "brazil", "mexico",
}  # fmt: skip
_REGIONS = {
    "bc", "b.c.", "british columbia", "ab", "alberta", "on", "ontario", "qc", "quebec",
    "québec", "mb", "manitoba", "sk", "saskatchewan", "ns", "nova scotia", "nb",
    "new brunswick", "nl", "newfoundland", "pe", "prince edward island", "yt", "yukon",
    "nt", "northwest territories", "nu", "nunavut", "ca", "wa", "or", "tx", "ny",
}  # fmt: skip


def _workday_metro_city(location: str | None, metro: Iterable[str]) -> str | None:
    """The configured metro municipality named in `location`, in config spelling."""
    text = (location or "").casefold()
    for name in metro:
        key = classify.norm_city(name)
        if key and re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", text):
            return name
    return None


def _workday_city(location: str | None, metro: Iterable[str]) -> str | None:
    """City from Workday's location text, which puts the parts in no fixed order:
    "Canada, BC, Vancouver", "Burnaby, British Columbia, Canada", "USA, CA, Pleasanton".
    A configured metro name wins; otherwise the first part that is not a country or region.
    """
    if re.fullmatch(r"\d+\s+locations?", (location or "").strip(), re.IGNORECASE):
        return None  # the list only says "4 Locations"; the detail names them
    found = _workday_metro_city(location, metro)
    if found:
        return found
    for part in re.split(r"[,;/|()]|\s+-\s+", location or ""):
        part = " ".join(part.split()).strip(" .")
        key = part.casefold()
        if (
            part
            and key not in _COUNTRIES
            and key not in _REGIONS
            and not re.fullmatch(r"[A-Z]{2}", part)
        ):
            return part
    return None


def _workday_id(external_path: str) -> str | None:
    """The posting id `links.py` takes: the part after the last `_` in the job slug."""
    slug = [s for s in external_path.split("/") if s]
    if not slug:
        return None
    return slug[-1].rsplit("_", 1)[1] if "_" in slug[-1] else slug[-1]


def _workday_workplace(remote_type: object, location: str | None) -> str:
    workplace = _workplace(remote_type)
    if workplace != "unknown":
        return workplace
    if str(remote_type or "").casefold().strip() == "flex":
        return "hybrid"  # Workday's "Flex" is a flexible, usually hybrid, arrangement
    loc = (location or "").casefold()
    if "remote" in loc:
        return "remote"
    if "hybrid" in loc:
        return "hybrid"
    return "unknown"


def _workday_date(value: object) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else None


def _workday_place(info: dict, bp: BoardPosting, metro: Iterable[str]) -> str | None:
    """The first of the posting's locations that is Metro Vancouver, then remote, then the
    main one. A multi-location posting may name Vancouver only in `additionalLocations`."""
    extra = info.get("additionalLocations")
    candidates = [info.get("location"), *(extra if isinstance(extra, list) else [])]
    candidates = [str(c) for c in candidates if c]
    for location in candidates:
        if _workday_metro_city(location, metro):
            return location
    for location in candidates:
        if "remote" in location.casefold():
            return location
    return candidates[0] if candidates else bp.location


def _workday_detail(info: dict, bp: BoardPosting, metro: Iterable[str]) -> BoardPosting:
    location = _workday_place(info, bp, metro)
    country = info.get("country")
    country_name = country.get("descriptor") if isinstance(country, dict) else None
    if "canada" in (location or "").casefold():
        country_name = "Canada"
    return replace(
        bp,
        title=" ".join(str(info.get("title") or bp.title).split()),
        url=info.get("externalUrl") or bp.url,
        location=location,
        city=_workday_city(location, metro) or bp.city,
        country=country_name or bp.country,
        workplace=_workday_workplace(info.get("remoteType"), location),
        description=text_from_html(info.get("jobDescription")),
        published_at=_workday_date(info.get("startDate")) or bp.published_at,
    )


def _workday_list_item(
    board: str, host: str, site: str, item: object, metro: Iterable[str], index: int
) -> tuple[BoardPosting, str]:
    if not isinstance(item, dict) or not item.get("title") or not item.get("externalPath"):
        raise ValueError(f"posting {index} has no title or externalPath")
    path = str(item["externalPath"])
    posting_id = _workday_id(path)
    if not posting_id:
        raise ValueError(f"posting {index} has no posting id")
    location = item.get("locationsText")
    bp = BoardPosting(
        ats="workday",
        board=board,
        posting_id=posting_id,
        title=" ".join(str(item["title"]).split()),
        url=f"https://{host}/{site}{path}",
        location=location,
        city=_workday_city(location, metro),
        workplace="remote" if "remote" in str(location or "").casefold() else "unknown",
    )
    return bp, path


def _workday_keep(bp: BoardPosting, terms: Iterable[str], metro: Iterable[str]) -> bool:
    """Would Scout keep this posting? (a superset covering WorkBC-linked postings)."""
    if not classify.term_hits(bp.title, terms):
        return False
    location = str(bp.location or "")
    if _workday_metro_city(location, metro):
        return True
    # "4 Locations" may include Vancouver; fetch the detail to find out.
    if re.fullmatch(r"\d+\s+locations?", location.strip(), re.IGNORECASE):
        return True
    loc = location.casefold()
    if "remote" not in loc:
        return False
    if "canada" in loc:
        return True
    # Remote with no country named is treated like the other boards (remote, unstated).
    return not any(re.search(rf"(?<![a-z]){re.escape(c)}(?![a-z])", loc) for c in _COUNTRIES)


def _outcome_for_status(status: int | None) -> str:
    if status in (401, 403):
        return "restricted"
    if status is None or status == 429 or status >= 500:
        return "transient"
    return "structural"


def _fetch_workday(
    board: str,
    *,
    host: str | None,
    terms: Iterable[str],
    metro: Iterable[str],
    keep_ids: set[str],
    get: Get,
    post: Post,
    sleep,
) -> BoardResult:
    res = BoardResult("workday", board)
    tenant, slash, site = board.partition("/")
    if not host or not slash or not tenant or not site:
        res.outcome, res.error = "structural", "workday board needs tenant/site and a host"
        return res
    if not WORKDAY_HOST.fullmatch(host):
        res.outcome, res.error = "structural", f"not a Workday host: {host!r}"
        return res
    terms = tuple(terms)
    if not terms:
        res.outcome, res.error = "structural", "workday search needs terms"
        return res
    metro = tuple(metro)
    search = WORKDAY.format(host=host, tenant=tenant, site=site)
    res.url = search
    seen: dict[str, BoardPosting] = {}
    paths: dict[str, str] = {}
    for term in terms:
        offset = 0
        while True:
            body = json.dumps(
                {"appliedFacets": {}, "limit": WORKDAY_PAGE_SIZE, "offset": offset,
                 "searchText": term}
            ).encode()  # fmt: skip
            raw, error, status = _post_with_retries(search, body, post, sleep)
            if raw is None:
                res.outcome, res.error, res.postings = _outcome_for_status(status), error, []
                return res
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                res.outcome, res.error, res.postings = "structural", f"unexpected response: {e}", []
                return res
            if not isinstance(data, dict) or not isinstance(data.get("jobPostings"), list):
                res.outcome = "structural"
                res.error = "unexpected response: no jobPostings list"
                res.postings = []
                return res
            items = data["jobPostings"]
            for i, item in enumerate(items):
                try:
                    bp, path = _workday_list_item(board, host, site, item, metro, i)
                except ValueError as e:
                    res.outcome, res.error, res.postings = (
                        "structural",
                        f"unexpected response: {e}",
                        [],
                    )
                    return res
                if bp.posting_id not in seen:
                    seen[bp.posting_id] = bp
                    paths[bp.posting_id] = path
            offset += WORKDAY_PAGE_SIZE
            total = data.get("total")
            if len(items) < WORKDAY_PAGE_SIZE or (
                isinstance(total, int) and not isinstance(total, bool) and offset >= total
            ):
                break
            sleep(1.0)
        sleep(1.0)
    # Details only for the postings Scout would keep (and any WorkBC posting on the board).
    for posting_id, bp in seen.items():
        if posting_id not in keep_ids and not _workday_keep(bp, terms, metro):
            continue
        sleep(1.0)
        url = WORKDAY_JOB.format(host=host, tenant=tenant, site=site, path=paths[posting_id])
        raw, _error, _status = _get_with_retries(url, get, sleep)
        if raw is None:  # keep the list's fields; one bad detail must not lose the board
            continue
        try:
            info = json.loads(raw).get("jobPostingInfo")
        except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            continue
        if isinstance(info, dict):
            seen[posting_id] = _workday_detail(info, bp, metro)
    res.postings = list(seen.values())
    res.outcome, res.error = ("ok" if res.postings else "empty"), None
    return res


def _retry(call: Callable[[], bytes], sleep) -> tuple[bytes | None, str | None, int | None]:
    """(body, error, http_status)"""
    error, status = None, None
    for attempt in range(RETRIES + 1):
        try:
            return call(), None, None
        except HttpError as e:
            status, error = e.status, f"HTTP {e.status}"
            if e.status not in (429,) and e.status < 500:
                return None, error, status
        except TransportError as e:
            status, error = None, str(e)
        if attempt < RETRIES:
            sleep(RETRY_WAIT * (attempt + 1))
    return None, error, status


def _get_with_retries(url: str, get: Get, sleep) -> tuple[bytes | None, str | None, int | None]:
    return _retry(lambda: get(url), sleep)


def _post_with_retries(
    url: str, body: bytes, post: Post, sleep
) -> tuple[bytes | None, str | None, int | None]:
    return _retry(lambda: post(url, body), sleep)


def fetch_board(
    ats: str,
    board: str,
    *,
    get: Get = http_get,
    post: Post = http_post,
    sleep=time.sleep,
    host: str | None = None,
    terms: Iterable[str] = (),
    metro: Iterable[str] = (),
    keep_ids: Iterable[str] = (),
) -> BoardResult:
    """Fetch and parse one board. Never raises for HTTP or data problems.

    `host`, `terms`, `metro` and `keep_ids` are used by Workday only: the host comes from a
    posting's link or the employer, and the rest from the search config and saved postings.
    """
    if ats not in SUPPORTED:
        return BoardResult(ats, board, "structural", error=f"unsupported ATS {ats!r}")
    if ats == "workday":
        return _fetch_workday(
            board, host=host, terms=terms, metro=metro, keep_ids=set(keep_ids),
            get=get, post=post, sleep=sleep,
        )  # fmt: skip
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
