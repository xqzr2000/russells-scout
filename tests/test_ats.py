"""Tests for the Ashby, Greenhouse and Lever board clients (task 1.8), on synthetic data."""

from __future__ import annotations

import json
from pathlib import Path

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
