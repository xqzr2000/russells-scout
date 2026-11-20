"""Tests for the Ashby, Greenhouse and Lever board clients (task 1.8), on synthetic data."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from scout.sources import ats
from scout.sources.workbc import HttpError, TransportError

FIX = Path(__file__).parent / "fixtures" / "ats"
ASHBY = (FIX / "ashby_northwind.json").read_bytes()
GREENHOUSE = (FIX / "greenhouse_contoso.json").read_bytes()
LEVER = (FIX / "lever_fabrikam.json").read_bytes()


class FakeGet:
    def __init__(self, responses: dict[str, list]):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        queue = self.responses.get(url)
        if not queue:
            raise HttpError(404)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return item


def fetch(ats_name, board, responses):
    get = FakeGet(responses)
    return ats.fetch_board(ats_name, board, get=get, sleep=lambda s: None), get


def test_ashby_board():
    url = ats.ASHBY.format(board="northwind")
    res, _ = fetch("ashby", "northwind", {url: [ASHBY]})
    assert res.outcome == "ok" and res.url == url
    by_title = {p.title: p for p in res.postings}
    assert "Data Analyst (internal)" not in by_title  # isListed false
    ds = by_title["Senior Data Scientist"]
    assert ds.posting_id == "11111111-2222-3333-4444-555555555555"
    assert (ds.city, ds.country, ds.workplace) == ("Vancouver", "Canada", "hybrid")  # not isRemote
    assert (ds.pay_min, ds.pay_max, ds.pay_currency, ds.pay_period) == (
        140000.0, 170000.0, "CAD", "year",
    )  # fmt: skip
    assert ds.description == "Build risk models. Python and SQL."
    ml = by_title["Machine Learning Engineer"]
    assert (ml.workplace, ml.city, ml.location) == ("remote", "Remote", "Remote (Canada)")
    assert by_title["Office Manager"].pay_min is None


def test_greenhouse_board_decodes_html_content():
    url = ats.GREENHOUSE.format(board="contoso")
    res, _ = fetch("greenhouse", "contoso", {url: [GREENHOUSE]})
    by_id = {p.posting_id: p for p in res.postings}
    ds = by_id["8525610002"]
    assert ds.city == "Vancouver" and ds.workplace == "unknown"
    assert ds.description == "Price things with statistics.\nPython\nSQL"
    assert by_id["8525610003"].workplace == "remote"
    assert ds.published_at.startswith("2026-10-13")


def test_lever_board_and_eu_fallback():
    eu = ats.LEVER_EU.format(board="fabrikam")
    res, get = fetch("lever", "fabrikam", {eu: [LEVER]})  # global host answers 404
    assert res.outcome == "ok" and res.url == eu
    assert get.urls[0] == ats.LEVER.format(board="fabrikam")
    de = res.postings[0]
    assert (de.title, de.city, de.country, de.workplace) == (
        "Data Engineer",
        "Vancouver",
        "CA",
        "hybrid",
    )
    assert (de.pay_min, de.pay_max, de.pay_period) == (110000.0, 135000.0, "year")
    assert "Build Spark jobs" in de.description and "$110,000" in de.description
    assert de.published_at.startswith("2025-10-27") or de.published_at.startswith("2025-10-28")
    assert res.postings[1].workplace == "onsite"


def test_missing_board_is_structural():
    res, _ = fetch("ashby", "nobody", {})
    assert res.outcome == "structural" and "board not found" in res.error


def test_transient_errors_are_retried_then_reported():
    url = ats.ASHBY.format(board="northwind")
    res, get = fetch("ashby", "northwind", {url: [HttpError(503), TransportError("reset"), ASHBY]})
    assert res.outcome == "ok" and len(get.urls) == 3
    res, _ = fetch("ashby", "northwind", {url: [HttpError(502)]})
    assert res.outcome == "transient"


def test_refusal_is_restricted():
    url = ats.GREENHOUSE.format(board="contoso")
    res, get = fetch("greenhouse", "contoso", {url: [HttpError(403)]})
    assert res.outcome == "restricted" and len(get.urls) == 1


@pytest.mark.parametrize("body", [b"<html>", b"[]", b'{"jobs": [{"title": "x"}]}'])
def test_unexpected_responses_are_structural(body):
    url = ats.ASHBY.format(board="northwind")
    res, _ = fetch("ashby", "northwind", {url: [body]})
    assert res.outcome == "structural" and "unexpected response" in res.error


def test_empty_board():
    url = ats.GREENHOUSE.format(board="contoso")
    res, _ = fetch("greenhouse", "contoso", {url: [b'{"jobs": [], "meta": {"total": 0}}']})
    assert res.outcome == "empty" and res.ok


def test_greenhouse_board_candidates():
    assert ats.greenhouse_board_candidates("Asana", "asana", "www.asana.com") == ["asana"]
    assert ats.greenhouse_board_candidates(
        "Connor, Clark & Lunn Financial Group Ltd.",
        "connor clark and lunn financial group",
        "careers.cclgroup.com",
    ) == ["connorclarkandlunnfinancialgroup", "connor-clark-and-lunn-financial-group", "connor",
          "cclgroup"]  # fmt: skip


def test_find_greenhouse_board_needs_the_exact_job():
    job = json.dumps({"id": 8275149, "title": "Data Scientist"}).encode()
    other = json.dumps({"id": 1, "title": "Something else"}).encode()
    get = FakeGet({
        ats.GREENHOUSE_JOB.format(board="asanainc", job_id="8275149"): [other],
        ats.GREENHOUSE_JOB.format(board="asana", job_id="8275149"): [job],
    })  # fmt: skip
    found = ats.find_greenhouse_board(
        "8275149", ["nope", "asanainc", "asana"], get=get, sleep=lambda s: None
    )
    assert found == "asana"
    assert ats.find_greenhouse_board("8275149", ["nope"], get=get, sleep=lambda s: None) is None


# --------------------------------------------------------------------------- Workday

WD_SEARCH = (FIX / "workday_fabrikam_search.json").read_bytes()
WD_SEARCH_ONE_BAD = (FIX / "workday_fabrikam_search_one_bad.json").read_bytes()
WD_SEARCH_ALL_BAD = (FIX / "workday_fabrikam_search_all_bad.json").read_bytes()
WD_JOB_123 = (FIX / "workday_fabrikam_job_123.json").read_bytes()
WD_JOB_125 = (FIX / "workday_fabrikam_job_125.json").read_bytes()
WD_JOB_126 = (FIX / "workday_fabrikam_job_126.json").read_bytes()
WD_HOST = "fabrikam.wd3.myworkdayjobs.com"
WD_BOARD = "fabrikam/Careers"
WD_SEARCH_URL = ats.WORKDAY.format(host=WD_HOST, tenant="fabrikam", site="Careers")
WD_JOB_URLS = {
    ats.WORKDAY_JOB.format(
        host=WD_HOST, tenant="fabrikam", site="Careers", path="/job/Vancouver-BC/Data-Scientist_R123"
    ): WD_JOB_123,
    ats.WORKDAY_JOB.format(
        host=WD_HOST, tenant="fabrikam", site="Careers", path="/job/Remote/Remote-Data-Engineer_R125"
    ): WD_JOB_125,
    ats.WORKDAY_JOB.format(
        host=WD_HOST, tenant="fabrikam", site="Careers",
        path="/job/Multi-Location/Machine-Learning-Engineer_R126",
    ): WD_JOB_126,
}  # fmt: skip


class FakePost:
    """Serves pages of one search fixture, sliced by offset/limit; records each body."""

    def __init__(self, data: bytes, details: dict[str, bytes], errors: list | None = None):
        self.data = data
        self.details = details
        self.errors = list(errors or [])
        self.urls: list[str] = []
        self.bodies: list[dict] = []

    def __call__(self, url: str, body: bytes) -> bytes:
        self.bodies.append(json.loads(body))
        if self.errors:
            item = self.errors.pop(0)
            if item is not None:
                raise item
        return self.data


def wd_get(details=WD_JOB_URLS):
    class Get:
        def __init__(self):
            self.urls: list[str] = []

        def __call__(self, url):
            self.urls.append(url)
            if url not in details:
                raise HttpError(404)
            return details[url]

    return Get()


def wd_fetch(
    terms=("data scientist", "data engineer", "machine learning"), post=None, get=None, **kw
):
    post = post or FakePost(WD_SEARCH, WD_JOB_URLS)
    get = get or wd_get()
    res = ats.fetch_board(
        "workday", WD_BOARD, host=WD_HOST, terms=terms, metro=("Vancouver", "Burnaby"),
        get=get, post=post, sleep=lambda s: None, **kw,
    )  # fmt: skip
    return res, post, get


def test_workday_board():
    res, post, get = wd_fetch()
    assert res.outcome == "ok" and res.url == WD_SEARCH_URL
    by_id = {p.posting_id: p for p in res.postings}
    assert set(by_id) == {"R123", "R124", "R125", "R126"}  # links.py's id: after the last "_"
    ds = by_id["R123"]
    assert ds.title == "Data Scientist" and ds.board == WD_BOARD
    assert ds.url.endswith("/Careers/job/Vancouver-BC/Data-Scientist_R123")
    assert (ds.location, ds.city, ds.country) == ("Canada, BC, Vancouver", "Vancouver", "Canada")
    assert ds.workplace == "hybrid"
    assert "Build models" in ds.description and "SQL" in ds.description
    assert ds.published_at == "2026-10-01"
    remote = by_id["R125"]
    assert (remote.city, remote.workplace, remote.country) == ("Remote", "remote", "Canada")
    # Vancouver is only in additionalLocations; the detail's main location is Pleasanton.
    ml = by_id["R126"]
    assert (ml.location, ml.city, ml.country) == ("Canada, BC, Vancouver", "Vancouver", "Canada")
    assert ml.workplace == "unknown"
    assert by_id["R124"].description is None  # Office Manager: not kept


def test_workday_paginates_each_search_to_a_fixed_end(monkeypatch):
    monkeypatch.setattr(ats, "WORKDAY_PAGE_SIZE", 2)
    res, post, _ = wd_fetch(terms=("data scientist",))
    assert res.outcome == "ok" and len(res.postings) == 4
    assert [b["offset"] for b in post.bodies] == [0, 2]  # two pages, then total reached
    assert all(b["searchText"] == "data scientist" for b in post.bodies)


def test_workday_stops_paging_at_the_cap(monkeypatch):
    """A board whose `total` is (wrongly) huge must not be paged forever."""
    monkeypatch.setattr(ats, "WORKDAY_PAGE_SIZE", 2)
    monkeypatch.setattr(ats, "WORKDAY_MAX_PAGES_PER_TERM", 3)
    bodies: list[dict] = []

    def post(url, body):
        req = json.loads(body)
        bodies.append(req)
        items = [
            {"title": "Data Scientist", "externalPath": f"/job/Vancouver-BC/Data-Scientist_R{n}"}
            for n in (1, 2)
        ]
        return json.dumps({"total": 1000, "jobPostings": items}).encode()

    res = ats.fetch_board(
        "workday", WD_BOARD, host=WD_HOST, terms=("data scientist",),
        metro=("Vancouver",), get=lambda url: WD_JOB_123, post=post, sleep=lambda s: None,
    )  # fmt: skip
    assert res.outcome == "ok"
    assert [b["offset"] for b in bodies] == [0, 2, 4]  # three pages, then the cap


def test_workday_location_from_external_path_when_list_omits_it(monkeypatch):
    """accenture search items carry no `locationsText`; the path still names the city."""
    item = {
        "title": "Data Scientist",
        "externalPath": "/job/Vancouver-BC/Data-Scientist_R999",
        "postedOn": "Posted Today",
        "bulletFields": ["R999"],
    }

    def post(url, body):
        return json.dumps({"total": 1, "jobPostings": [item]}).encode()

    res = ats.fetch_board(
        "workday", WD_BOARD, host=WD_HOST, terms=("data scientist",),
        metro=("Vancouver", "Burnaby"), get=lambda url: WD_JOB_123, post=post,
        sleep=lambda s: None,
    )  # fmt: skip
    assert res.outcome == "ok"
    assert [p.posting_id for p in res.postings] == ["R999"]
    assert res.postings[0].city == "Vancouver"  # from the path, before the detail arrives


def test_workday_uses_every_configured_term(monkeypatch):
    monkeypatch.setattr(ats, "WORKDAY_PAGE_SIZE", 2)  # fixture has 4 jobs: 2 pages per term
    terms = ("data scientist", "machine learning", "data engineer")
    _, post, _ = wd_fetch(terms=terms)
    assert list(dict.fromkeys(b["searchText"] for b in post.bodies)) == list(terms)
    assert all([b["searchText"] for b in post.bodies].count(t) == 2 for t in terms)  # 2 pages


def test_workday_fetches_details_only_for_kept_jobs():
    res, _, get = wd_fetch(terms=("data scientist", "data engineer", "machine learning"))
    assert set(get.urls) == set(WD_JOB_URLS)  # Office Manager (R124) has no detail fetch
    assert {p.posting_id for p in res.postings} == {"R123", "R124", "R125", "R126"}


def test_workday_must_fetch_workbc_linked_postings():
    # A posting WorkBC already has must get its details even if the area filter misses it.
    post = FakePost(WD_SEARCH, WD_JOB_URLS)
    get = wd_get(details=WD_JOB_URLS)
    res = ats.fetch_board(
        "workday", WD_BOARD, host=WD_HOST, terms=("data scientist",), metro=(),
        keep_ids={"R124"}, get=get, post=post, sleep=lambda s: None,
    )  # fmt: skip
    assert any(url.endswith("Office-Manager_R124") for url in get.urls)
    assert res.outcome == "ok"


@pytest.mark.parametrize(
    ("error", "outcome"),
    [(HttpError(401), "restricted"), (HttpError(403), "restricted"),
     (HttpError(404), "structural"), (HttpError(500), "transient"),
     (HttpError(429), "transient"), (TransportError("reset"), "transient")],
)  # fmt: skip
def test_workday_failures_map_like_other_boards(error, outcome):
    post = FakePost(WD_SEARCH, {}, errors=[error] * (ats.RETRIES + 1))
    res, _, _ = wd_fetch(post=post)
    assert res.outcome == outcome


def test_workday_skips_an_unusable_item_and_keeps_the_board():
    res, _, _ = wd_fetch(post=FakePost(WD_SEARCH_ONE_BAD, {}), terms=("data analyst",))
    assert res.outcome == "ok" and res.error is None
    assert [p.posting_id for p in res.postings] == ["R201"]
    assert res.skipped == 1


def test_workday_a_page_with_no_usable_item_is_structural():
    res, _, _ = wd_fetch(post=FakePost(WD_SEARCH_ALL_BAD, {}), terms=("data analyst",))
    assert res.outcome == "structural" and "unusable" in res.error
    assert res.postings == [] and res.skipped == 3


def test_workday_unexpected_response_is_structural():
    res, _, _ = wd_fetch(post=FakePost(b"<html>", {}))
    assert res.outcome == "structural" and "unexpected response" in res.error
    res, _, _ = wd_fetch(post=FakePost(b'{"total": 1}', {}))
    assert res.outcome == "structural"


class FlakyPost(FakePost):
    """Returns the given non-JSON bodies first, then serves the usual search fixture."""

    def __init__(self, bad: list[bytes], data: bytes = WD_SEARCH, details=None):
        super().__init__(data, details or WD_JOB_URLS)
        self.bad = list(bad)

    def __call__(self, url: str, body: bytes) -> bytes:
        if self.bad:
            self.bodies.append(json.loads(body))
            return self.bad.pop(0)
        return super().__call__(url, body)


def test_workday_retries_a_non_json_search_response():
    # An outage page or empty body for one request must not lose the board.
    post = FlakyPost([b"<html>maintenance</html>"])
    res, post, _ = wd_fetch(post=post)
    assert res.outcome == "ok" and len(res.postings) == 4
    assert len(post.bodies) == 4  # one page each for 3 terms, plus the retried first request


def test_workday_bounded_retries_then_structural():
    post = FlakyPost([b""] * (ats.RETRIES + 1))
    res, post, _ = wd_fetch(post=post, terms=("data scientist",))
    assert res.outcome == "structural" and "unexpected response" in res.error
    assert len(post.bodies) == ats.RETRIES + 1  # retried, then gave up (no page 2)


def test_workday_needs_a_host_and_terms():
    res = ats.fetch_board("workday", WD_BOARD, sleep=lambda s: None)
    assert res.outcome == "structural" and "host" in res.error
    res = ats.fetch_board("workday", WD_BOARD, host=WD_HOST, sleep=lambda s: None)
    assert res.outcome == "structural" and "terms" in res.error


@pytest.mark.parametrize(
    "host",
    ["jobs.lever.co", "10.0.0.181", "localhost", "fabrikam.wd3.myworkdayjobs.com.evil.example",
     "evil.example/fabrikam.wd3.myworkdayjobs.com", "FABRIKAM.wd3.myworkdayjobs.com:8080"],
)  # fmt: skip
def test_workday_only_talks_to_workday_hosts(host):
    # The host comes from data (a posting link or an employer's careers host).
    post, get = FakePost(WD_SEARCH, WD_JOB_URLS), wd_get()
    res = ats.fetch_board(
        "workday", WD_BOARD, host=host, terms=("data scientist",), get=get, post=post,
        sleep=lambda s: None,
    )  # fmt: skip
    assert res.outcome == "structural" and "not a Workday host" in res.error
    assert post.bodies == [] and get.urls == []


def test_workday_keep_only_fetches_plausible_remote():
    def bp(title, location):
        return ats.BoardPosting("workday", WD_BOARD, "R1", title, "https://x", location=location)

    metro = ("Vancouver", "Burnaby")
    terms = ("data scientist",)
    assert ats._workday_keep(bp("Data Scientist", "Canada, BC, Vancouver"), terms, metro)
    assert ats._workday_keep(bp("Data Scientist", "Remote, Canada"), terms, metro)
    assert ats._workday_keep(bp("Data Scientist", "Remote"), terms, metro)
    assert ats._workday_keep(bp("Data Scientist", "4 Locations"), terms, metro)
    # Remote in another country, or not remote at all, is not a job Scout would keep.
    assert not ats._workday_keep(bp("Data Scientist", "Remote, USA"), terms, metro)
    assert not ats._workday_keep(bp("Data Scientist", "Toronto, Ontario, Canada"), terms, metro)
    assert not ats._workday_keep(bp("Office Manager", "Remote, Canada"), terms, metro)


def test_workday_city_ignores_country_and_region_parts():
    assert ats._workday_city("USA, CA, Pleasanton", ()) == "Pleasanton"
    assert ats._workday_city("Toronto, Ontario, Canada", ()) == "Toronto"
    assert ats._workday_city("Canada, BC, Vancouver", ("Vancouver",)) == "Vancouver"
    assert ats._workday_city("Support Office Vancouver", ("Vancouver",)) == "Vancouver"


AMAZON = (FIX / "amazon_search.json").read_bytes()
AMAZON_ONE_BAD = (FIX / "amazon_search_one_bad.json").read_bytes()
AMAZON_ALL_BAD = (FIX / "amazon_search_all_bad.json").read_bytes()


class AmazonGet:
    """Serves the jobs of one fixture sliced by offset/result_limit; records each URL."""

    def __init__(self, data: bytes):
        self.raw = data
        try:
            self.data = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.data = None
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        query = parse_qs(urlparse(url).query)
        if not isinstance(self.data, dict) or not isinstance(self.data.get("jobs"), list):
            return self.raw  # malformed or wrong shape: serve it untouched
        offset = int(query["offset"][0])
        limit = int(query["result_limit"][0])
        page = dict(self.data)
        page["jobs"] = self.data["jobs"][offset : offset + limit]
        return json.dumps(page).encode()


class AmazonErrorGet:
    def __init__(self, error: Exception):
        self.error = error
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        raise self.error


def amz_fetch(get=None, terms=("data scientist",)):
    get = get or AmazonGet(AMAZON)
    res = ats.fetch_board("amazon", "amazon", terms=terms, get=get, sleep=lambda s: None)
    return res, get


def test_amazon_board():
    res, get = amz_fetch()
    assert res.outcome == "ok" and res.error is None and res.board == "amazon"
    by_id = {p.posting_id: p for p in res.postings}
    assert set(by_id) == {"10000001", "10000002", "10000003", "10000004"}
    ds = by_id["10000001"]
    assert ds.ats == "amazon" and ds.board == "amazon"
    assert ds.title == "Data Scientist, Fabrikam Analytics"
    assert ds.url == "https://www.amazon.jobs/en/jobs/10000001/fabrikam-data-scientist"
    assert ds.location == "Vancouver, British Columbia, CAN"
    assert (ds.city, ds.country, ds.workplace) == ("Vancouver", "CAN", "unknown")
    assert ds.published_at == "2026-11-09"
    assert "Build models" in ds.description and "<br" not in ds.description
    assert by_id["10000004"].workplace == "remote"
    assert by_id["10000003"].city == "Victoria"


def test_amazon_paginates_to_a_fixed_end(monkeypatch):
    monkeypatch.setattr(ats, "AMAZON_PAGE_SIZE", 2)  # fixture has 4 jobs: 2 pages
    res, get = amz_fetch()
    assert res.outcome == "ok" and len(res.postings) == 4
    offsets = [parse_qs(urlparse(u).query)["offset"][0] for u in get.urls]
    assert offsets == ["0", "2"]


def test_amazon_uses_every_configured_term_and_dedups():
    res, get = amz_fetch(terms=("data scientist", "machine learning"))
    assert res.outcome == "ok" and len(res.postings) == 4  # same jobs, not eight
    queries = [parse_qs(urlparse(u).query)["base_query"][0] for u in get.urls]
    assert queries == ["data scientist", "machine learning"]
    assert all("vancouvervictoria-canada" in u for u in get.urls)


def test_amazon_skips_an_unusable_item_and_keeps_the_board():
    res, _ = amz_fetch(AmazonGet(AMAZON_ONE_BAD))
    assert res.outcome == "ok" and [p.posting_id for p in res.postings] == ["10000011"]
    assert res.skipped == 1


def test_amazon_a_page_with_no_usable_item_is_structural():
    res, _ = amz_fetch(AmazonGet(AMAZON_ALL_BAD))
    assert res.outcome == "structural" and "unusable" in res.error
    assert res.postings == [] and res.skipped == 2


def test_amazon_unexpected_response_is_structural():
    res, _ = amz_fetch(AmazonGet(b"<html>"))
    assert res.outcome == "structural" and "unexpected response" in res.error
    res, _ = amz_fetch(AmazonGet(b'{"hits": 1}'))
    assert res.outcome == "structural"


@pytest.mark.parametrize(
    ("error", "outcome"),
    [(HttpError(401), "restricted"), (HttpError(403), "restricted"),
     (HttpError(404), "structural"), (HttpError(500), "transient"),
     (HttpError(429), "transient"), (TransportError("reset"), "transient")],
)  # fmt: skip
def test_amazon_failures_map_like_other_boards(error, outcome):
    res, _ = amz_fetch(AmazonErrorGet(error))
    assert res.outcome == outcome


# ------------------------------------------------------------- Retry-After and 429s


def test_retry_honours_retry_after_on_429():
    calls = [HttpError(429, b"", 7.0), WD_SEARCH]
    sleeps = []

    def call():
        item = calls.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    body, error, status = ats._retry(call, sleeps.append)
    assert (body, error, status) == (WD_SEARCH, None, None)
    assert sleeps == [7.0]


def test_retry_caps_retry_after_and_keeps_plain_backoff():
    def run(errors):
        calls = [*errors, WD_SEARCH]
        sleeps = []

        def call():
            item = calls.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        assert ats._retry(call, sleeps.append)[0] == WD_SEARCH
        return sleeps

    capped = run([HttpError(429, b"", 999.0), HttpError(429, b"", 999.0)])
    assert capped == [ats.MAX_RETRY_AFTER, ats.MAX_RETRY_AFTER]
    plain = run([HttpError(429), HttpError(429)])
    assert plain == [ats.RETRY_WAIT, ats.RETRY_WAIT * 2]


def test_workday_waits_retry_after_before_retrying_a_search():
    post = FakePost(WD_SEARCH, WD_JOB_URLS, errors=[HttpError(429, b"", 7.0)])
    sleeps: list[float] = []
    res = ats.fetch_board(
        "workday", WD_BOARD, host=WD_HOST, terms=("data scientist",),
        metro=("Vancouver", "Burnaby"), get=wd_get(), post=post, sleep=sleeps.append,
    )  # fmt: skip
    assert res.outcome == "ok"
    assert 7.0 in sleeps  # the board's Retry-After, not the default 5s


def test_open_carries_retry_after(monkeypatch):
    import email.message
    import io
    import urllib.error
    import urllib.request

    headers = email.message.Message()
    headers["Retry-After"] = "12"

    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(
            "https://example.com/x", 429, "Too Many Requests", headers, io.BytesIO(b"throttled")
        )

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(HttpError) as info:
        ats._open(urllib.request.Request("https://example.com/x"))
    assert info.value.status == 429 and info.value.retry_after == 12.0
    assert info.value.body == b"throttled"


# ------------------------------------------------------------- Workday: limit to Canada

CANADA = "c4f78be1a8f14da0ab49ce1162348a5e"  # synthetic facet id
COUNTRY_FACET = {
    "facetParameter": "locationCountry",
    "descriptor": "Country",
    "values": [
        {"descriptor": "United States of America", "id": "bc33aa3152ec42d4995f4791a106ed09",
         "count": 1500},
        {"descriptor": "Canada", "id": CANADA, "count": 39},
    ],
}  # fmt: skip
LONDON_JOB = {
    "title": "Data Scientist",
    "externalPath": "/job/London/Data-Scientist_R900",
    "locationsText": "London, United Kingdom",
    "bulletFields": ["R900"],
}


class CountryPost:
    """Worldwide searches (huge total, a London job, the country facet); Canada-only ones."""

    def __init__(self, facets_for=lambda term: [COUNTRY_FACET]):
        self.facets_for = facets_for
        self.bodies: list[dict] = []

    def __call__(self, url, body):
        req = json.loads(body)
        self.bodies.append(req)
        if req["appliedFacets"]:
            return WD_SEARCH  # the fixture's four Vancouver/Canada jobs
        page = {
            "total": 2000,
            "jobPostings": [LONDON_JOB] * ats.WORKDAY_PAGE_SIZE,
            "facets": self.facets_for(req["searchText"]),
        }
        return json.dumps(page).encode()


def test_workday_limits_a_board_to_canada():
    # 2026-11-14 to 11-19: accenture's worldwide searches (~850 requests a run) got every
    # Workday board throttled. With the Canada facet it's one page per term.
    post = CountryPost()
    res, _, _ = wd_fetch(post=post, terms=("data scientist", "machine learning"))
    assert res.outcome == "ok"
    applied = [b["appliedFacets"] for b in post.bodies]
    assert applied == [{}, {"locationCountry": [CANADA]}, {"locationCountry": [CANADA]}]
    assert [b["searchText"] for b in post.bodies] == [
        "data scientist", "data scientist", "machine learning",
    ]  # fmt: skip
    # Nothing is kept from the worldwide page.
    assert sorted(p.posting_id for p in res.postings) == ["R123", "R124", "R125", "R126"]


def test_workday_finds_a_country_facet_inside_a_group():
    group = {"facetParameter": "locationMainGroup", "values": [
        {"facetParameter": "locations", "descriptor": "Locations", "values": []},
        COUNTRY_FACET,
    ]}  # fmt: skip
    assert ats._workday_country_facet([group]) == {"locationCountry": [CANADA]}


def test_workday_looks_for_the_facet_on_each_term_until_found():
    # Facets list only countries with hits for the term; Canada shows up for the second.
    post = CountryPost(lambda term: [COUNTRY_FACET] if term == "machine learning" else [])
    res, _, _ = wd_fetch(post=post, terms=("data scientist", "machine learning", "data engineer"))
    assert res.outcome == "ok"
    seen = [(b["searchText"], bool(b["appliedFacets"])) for b in post.bodies]
    assert seen[-3:] == [
        ("machine learning", False), ("machine learning", True), ("data engineer", True),
    ]  # fmt: skip
    # The first term ran worldwide, up to the page cap.
    assert seen[: ats.WORKDAY_MAX_PAGES_PER_TERM] == [("data scientist", False)] * (
        ats.WORKDAY_MAX_PAGES_PER_TERM
    )


@pytest.mark.parametrize(
    "facets",
    [
        {},  # the fixture's shape: no facet list at all
        [],
        [{"facetParameter": "jobFamilyGroup", "values": [{"descriptor": "Canada", "id": "x"}]}],
        [{**COUNTRY_FACET, "values": [{"descriptor": "France", "id": "f1"}]}],
        [{**COUNTRY_FACET, "values": [{"descriptor": "Canada", "id": "bad id; drop"}]}],
        [{**COUNTRY_FACET, "facetParameter": "location Country!"}],
        "not a list",
    ],
)
def test_workday_without_a_usable_canada_facet_searches_worldwide(facets):
    assert ats._workday_country_facet(facets) is None


def test_workday_counts_detail_pages_that_dont_load():
    # 2026-11-19: remitly's search answered but no detail page did (6 matched, 0 descriptions).
    res, _, _ = wd_fetch(get=wd_get(details={}))  # every detail GET is a 404
    assert res.outcome == "ok" and len(res.postings) == 4
    assert res.details_failed == 3  # the three postings Scout would keep
    assert all(p.description is None for p in res.postings)


# -------------------------------------------------------------- Workday: throttling (429)


def test_workday_search_429_marks_the_board_throttled():
    post = FakePost(WD_SEARCH, WD_JOB_URLS, errors=[HttpError(429)] * (ats.RETRIES + 1))
    res, post, get = wd_fetch(post=post)
    assert (res.outcome, res.error, res.throttled) == ("transient", "HTTP 429", True)
    assert len(post.bodies) == ats.RETRIES + 1 and get.urls == []


def test_workday_other_failures_are_not_throttling():
    post = FakePost(WD_SEARCH, WD_JOB_URLS, errors=[HttpError(503)] * (ats.RETRIES + 1))
    res, _, _ = wd_fetch(post=post)
    assert res.outcome == "transient" and not res.throttled


def test_workday_stops_fetching_details_after_a_429():
    # 2026-11-20: once Workday throttles, every further request collects a 429 and keeps the
    # throttle going. The list's fields are kept for all of them.
    class Throttled:
        def __init__(self):
            self.urls: list[str] = []

        def __call__(self, url):
            self.urls.append(url)
            raise HttpError(429)

    get = Throttled()
    res, _, _ = wd_fetch(get=get)
    assert res.outcome == "ok" and len(res.postings) == 4 and res.throttled
    assert len(get.urls) == ats.RETRIES + 1  # the first detail page only, with its retries
    assert res.details_failed == 3  # all three postings Scout would keep


# ---------------------------------------- Workday: WorkBC postings the capped search misses

R999_PATH = "/job/Vancouver-BC/Senior-Data-Scientist_R999"
R999_URL = ats.WORKDAY_JOB.format(host=WD_HOST, tenant="fabrikam", site="Careers", path=R999_PATH)


@pytest.mark.parametrize(
    "url, path",
    [
        (f"https://{WD_HOST}/Careers{R999_PATH}", R999_PATH),
        (f"https://{WD_HOST}/en-US/Careers{R999_PATH}/?source=WorkBC", R999_PATH),
        (f"https://{WD_HOST}/Careers/job/Vancouver-BC/Other_R1000", None),  # another posting
        (f"https://{WD_HOST}/Careers/details/Senior-Data-Scientist_R999", None),
        (f"https://{WD_HOST}/Careers/job/../x/Senior-Data-Scientist_R999", None),
        (f"https://{WD_HOST}/Careers/job/Senior-Data-Scientist_R999", None),  # no location
        (None, None),
    ],
)
def test_workday_job_path_from_a_workbc_link(url, path):
    assert ats.workday_job_path(url, "R999") == path


def test_workday_fetches_a_workbc_posting_the_search_missed():
    # 2026-11-20: big boards with no Canada facet are searched worldwide, 3 pages per term,
    # so a WorkBC-linked Vancouver posting can rank past them. Its link names the detail page.
    get = wd_get({**WD_JOB_URLS, R999_URL: WD_JOB_123})
    res, _, get = wd_fetch(get=get, keep_ids={"R999"}, keep_paths={"R999": R999_PATH})
    assert res.outcome == "ok" and res.details_failed == 0
    r999 = {p.posting_id: p for p in res.postings}["R999"]
    assert r999.title == "Data Scientist"  # from the detail page
    assert r999.description and "Build models" in r999.description
    assert get.urls[-1] == R999_URL
    # One the search did find isn't fetched twice.
    res, _, get = wd_fetch(
        keep_ids={"R123"}, keep_paths={"R123": "/job/Vancouver-BC/Data-Scientist_R123"}
    )
    assert len(get.urls) == len(set(get.urls))


def test_workday_workbc_posting_whose_page_is_gone_is_left_out():
    res, _, _ = wd_fetch(keep_ids={"R999"}, keep_paths={"R999": R999_PATH})  # 404
    assert res.outcome == "ok" and "R999" not in {p.posting_id for p in res.postings}
    assert res.details_failed == 1


def test_workday_throttled_board_doesnt_fetch_missed_workbc_postings():
    class Throttled:
        def __init__(self):
            self.urls: list[str] = []

        def __call__(self, url):
            self.urls.append(url)
            raise HttpError(429)

    get = Throttled()
    res, _, _ = wd_fetch(get=get, keep_ids={"R999"}, keep_paths={"R999": R999_PATH})
    assert res.throttled and R999_URL not in get.urls
