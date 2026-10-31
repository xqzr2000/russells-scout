"""Tests for the WorkBC client (task 1.3), on synthetic data only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.sources import workbc
from scout.sources.workbc import HttpError, TransportError

FIXTURES = Path(__file__).parent / "fixtures" / "workbc"


def job(n: int, title: str = "Data Scientist", **extra) -> dict:
    return {"JobId": f"job{n:04d}", "Title": title, "City": "Vancouver", **extra}


class FakeWorkBC:
    """Serves pages like the real API. `failures[(term, page)]` is a list of exceptions or
    raw bytes returned before the real page (one per request)."""

    def __init__(self, data: dict[str, list[dict]], failures=None, key_case="lower"):
        self.data = data
        self.failures = {k: list(v) for k, v in (failures or {}).items()}
        self.key_case = key_case
        self.requests: list[dict] = []

    def __call__(self, url: str, body: dict) -> bytes:
        self.requests.append(body)
        term, page, size = body["Keyword"], body["Page"], body["PageSize"]
        queued = self.failures.get((term, page))
        if queued:
            item = queued.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        jobs = self.data.get(term, [])
        chunk = jobs[(page - 1) * size : page * size]
        keys = ("result", "count") if self.key_case == "lower" else ("Result", "Count")
        return json.dumps({keys[0]: chunk, keys[1]: len(jobs), "pageNumber": page}).encode()


def run(fake, terms, page_size=2, max_pages=10, raw_dir=None):
    sleeps: list[float] = []
    result = workbc.sweep(
        terms,
        page_size=page_size,
        max_pages=max_pages,
        delay=1.0,
        post=fake,
        sleep=sleeps.append,
        raw_dir=raw_dir,
        clock=lambda: 0.0,
    )
    return result, sleeps


def test_request_body_matches_the_api():
    assert workbc.search_body("data scientist", 2, 50) == {
        "Page": 2,
        "PageSize": 50,
        "Keyword": "data scientist",
        "SearchInField": "title",
        "SortOrder": 1,
    }


def test_pages_until_count_and_waits_between_requests():
    fake = FakeWorkBC({"data scientist": [job(i) for i in range(5)]})
    result, sleeps = run(fake, ["data scientist"])
    t = result.terms[0]
    assert (t.outcome, t.complete, t.count, t.pages, len(t.jobs)) == ("ok", True, 5, 3, 5)
    assert [r["Page"] for r in fake.requests] == [1, 2, 3]
    assert sleeps == [1.0, 1.0]  # no wait before the very first request
    assert result.complete


def test_empty_term_is_ok_and_complete():
    result, _ = run(FakeWorkBC({"data scientist": [job(1)]}), ["data scientist", "quant"])
    assert result.terms[1].outcome == "empty" and result.terms[1].complete
    assert result.complete and not result.suspicious_empty


def test_all_terms_empty_is_suspicious():
    result, _ = run(FakeWorkBC({}), ["a", "b"])
    assert result.suspicious_empty and not result.complete


def test_merges_postings_found_by_several_terms():
    shared = job(7, "Machine Learning Data Scientist")
    fake = FakeWorkBC({"data scientist": [job(1), shared], "machine learning": [shared]})
    result, _ = run(fake, ["data scientist", "machine learning"])
    assert len(result.jobs) == 2
    assert result.matched_terms["job0007"] == ["data scientist", "machine learning"]
    assert result.matched_terms["job0001"] == ["data scientist"]


def test_capitalized_outer_keys_are_accepted():
    result, _ = run(FakeWorkBC({"a": [job(1)]}, key_case="upper"), ["a"])
    assert result.terms[0].complete


def test_stopping_at_max_pages_is_incomplete():
    result, _ = run(FakeWorkBC({"a": [job(i) for i in range(9)]}), ["a"], max_pages=2)
    t = result.terms[0]
    assert t.ok and not t.complete and "stopped at 2 pages with 4 of 9" in t.error
    assert not result.complete


def test_transient_errors_are_retried():
    fake = FakeWorkBC(
        {"a": [job(1)]},
        failures={("a", 1): [HttpError(503), TransportError("timed out")]},
    )
    result, sleeps = run(fake, ["a"])
    assert result.terms[0].complete and result.terms[0].error is None
    assert len(fake.requests) == 3
    assert workbc.RETRY_WAIT in sleeps


def test_persistent_failure_is_transient_and_others_continue():
    fake = FakeWorkBC(
        {"a": [job(1)], "b": [job(2)]},
        failures={("a", 1): [HttpError(502)] * 3},
    )
    result, _ = run(fake, ["a", "b"])
    assert result.terms[0].outcome == "transient" and "HTTP 502" in result.terms[0].error
    assert result.terms[1].complete
    assert list(result.jobs) == ["job0002"]
    assert not result.complete


@pytest.mark.parametrize("status", [401, 403])
def test_refusal_is_restricted_without_retry(status):
    fake = FakeWorkBC({"a": [job(1)]}, failures={("a", 1): [HttpError(status)]})
    result, _ = run(fake, ["a"])
    assert result.terms[0].outcome == "restricted"
    assert len(fake.requests) == 1


def test_bad_request_means_the_api_changed():
    fake = FakeWorkBC(
        {"a": [job(1)]},
        failures={("a", 1): [HttpError(400, b"Unexpected parameters in the request!")]},
    )
    result, _ = run(fake, ["a"])
    t = result.terms[0]
    assert t.outcome == "structural" and "Unexpected parameters" in t.error


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"<html>maintenance</html>", "not JSON"),
        (b"[]", "not a JSON object"),
        (b'{"count": 1}', "no result list"),
        (b'{"result": [], "count": "many"}', "count is not a number"),
        (b'{"result": [{"Title": "x"}], "count": 1}', "job 0 has no JobId"),
    ],
)
def test_unexpected_responses_are_structural(raw, message):
    fake = FakeWorkBC({"a": [job(1)], "b": [job(2)]}, failures={("a", 1): [raw]})
    result, _ = run(fake, ["a", "b"])
    assert result.terms[0].outcome == "structural" and message in result.terms[0].error
    assert result.terms[1].complete  # isolation: the next term still runs


def test_postings_closing_mid_sweep_leave_term_incomplete():
    # WorkBC said 3 but page 2 comes back empty.
    fake = FakeWorkBC({"a": [job(1), job(2), job(3)]})
    real = fake.__call__

    def post(url, body):
        raw = json.loads(real(url, body))
        if body["Page"] == 2:
            raw["result"] = []
        return json.dumps(raw).encode()

    result = workbc.sweep(["a"], page_size=2, max_pages=5, delay=1, post=post, sleep=lambda s: 0)
    t = result.terms[0]
    assert t.ok and not t.complete and "reported 3 but returned 2" in t.error


def test_raw_pages_are_saved(tmp_path):
    result, _ = run(
        FakeWorkBC({"AI engineer": [job(1), job(2), job(3)]}),
        ["AI engineer"],
        raw_dir=tmp_path / "run1",
    )
    saved = sorted(p.name for p in (tmp_path / "run1").iterdir())
    assert saved == ["ai_engineer-p1.json", "ai_engineer-p2.json"]
    assert result.raw_dir == tmp_path / "run1"


def test_unwritable_raw_dir_only_warns(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    result, _ = run(FakeWorkBC({"a": [job(1)]}), ["a"], raw_dir=blocker / "run")
    assert result.complete and result.raw_dir is None
    assert result.warnings and "not saving raw responses" in result.warnings[0]


def test_fixture_page_parses_and_jump_urls():
    raw = (FIXTURES / "page_mixed.json").read_bytes()
    result, _ = run(FakeWorkBC({}, failures={("x", 1): [raw]}), ["x"], page_size=50)
    jobs = result.jobs
    assert result.terms[0].complete and len(jobs) == 3
    assert workbc.jump_url(jobs["cmexample0001aaaaaaaaaaaaa"]).startswith(
        "https://jobs.ashbyhq.com/northwind/"
    )
    assert workbc.jump_url(jobs["50000001"]) is None  # Job Bank: no employer link


# --------------------------------------------------------------------------- scout sweep


def test_dry_run_summary(monkeypatch, tmp_path):
    from scout import sweep

    page = (FIXTURES / "page_mixed.json").read_bytes()
    empty = json.dumps({"result": [], "count": 0}).encode()
    monkeypatch.setenv("SCOUT_RAW_DIR", str(tmp_path))
    lines: list[str] = []
    code = sweep.run(
        True,
        terms=["data scientist", "data analyst"],
        out=lines.append,
        post=lambda url, body: page if body["Keyword"] == "data scientist" else empty,
        sleep=lambda s: None,
    )
    text = "\n".join(lines)
    assert code == 0, text
    assert "unique postings: 3" in text
    assert "Job Bank postings: 1" in text
    assert "jobs.ashbyhq.com 1" in text and "(no link) 1" in text
    assert "complete sweep: yes" in text
    assert len(list(tmp_path.glob("sweep-*/data_scientist-p1.json"))) == 1


def test_sweep_without_dry_run_is_not_ready_yet():
    from scout import sweep

    lines: list[str] = []
    assert sweep.run(False, out=lines.append) == 2


def test_unknown_term_is_refused():
    from scout import sweep

    lines: list[str] = []
    assert sweep.run(True, terms=["astronaut"], out=lines.append) == 1
    assert "not in search.json: astronaut" in lines[0]
