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
