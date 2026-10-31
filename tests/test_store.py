"""Tests for saving sweeps (task 1.6). Database tests run with SCOUT_TEST_DB=1.

Each test works inside one outer transaction that is rolled back, so the database is left
as it was (scout_ingest can't delete). save_sweep's own transaction becomes a savepoint.
Acceptance tests from the plan: 2 (idempotent rerun) and 4 (removal safety).
"""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest

from scout import config, db, store
from scout.pipeline.record import build
from scout.sources import workbc
from scout.sources.workbc import HttpError

REPO = Path(__file__).resolve().parents[1]
CFG = config.load(REPO / "config" / "search.json")
TERMS = ["data scientist", "data analyst"]


def job(n, title="Data Scientist", city="Vancouver", employer="Northwind Analytics Inc.", **kw):
    j = {
        "JobId": f"t{n:04d}",
        "Title": title,
        "City": city,
        "EmployerName": employer,
        "IsFederalJob": False,
        "SalarySummary": "$100,000 annually",
        "ExternalSource": {
            "Source": [{"Source": "jobs.ashbyhq.com", "Url": f"https://jobs.ashbyhq.com/nw/{n}"}]
        },
    }
    j.update(kw)
    return j


def sweep_of(by_term: dict[str, list[dict]], fail: set[str] = frozenset()):
    """Run the real sweep code against an in-memory WorkBC; `fail` terms return 503."""

    def post(url, body):
        term = body["Keyword"]
        if term in fail:
            raise HttpError(503)
        jobs = by_term.get(term, [])
        return json.dumps({"result": jobs, "count": len(jobs)}).encode()

    terms = list(by_term) + [t for t in fail if t not in by_term]
    result = workbc.sweep(
        terms, page_size=50, max_pages=5, delay=0.5, post=post, sleep=lambda s: None
    )
    records = [build(j, result.matched_terms[i], CFG) for i, j in result.jobs.items()]
    return result, records


def test_content_hash_follows_watched_fields():
    _, (rec,) = sweep_of({"data scientist": [job(1)]})
    same = store.content_hash(rec)
    assert store.content_hash(dataclasses.replace(rec, area="remote")) == same
    assert store.content_hash(dataclasses.replace(rec, title="Lead Data Scientist")) != same


def test_source_outcome_picks_the_worst():
    result, _ = sweep_of({"data scientist": [job(1)]}, fail={"data analyst"})
    outcome, error = store.source_outcome(result)
    assert outcome == "transient" and "data analyst" in error
    result, _ = sweep_of({"a": [], "b": []})
    assert store.source_outcome(result)[0] == "structural"


def test_keep_follows_area_settings():
    _, recs = sweep_of(
        {"data scientist": [job(1), job(2, city="Victoria"), job(3, city="British Columbia")]}
    )
    by_city = {r.city: r for r in recs}
    no_other = dataclasses.replace(
        CFG, area=dataclasses.replace(CFG.area, keep_other_bc=False, include_remote=False)
    )
    assert store.keep(by_city["Victoria"], CFG)
    assert not store.keep(by_city["Victoria"], no_other)
    assert not store.keep(by_city["British Columbia"], no_other)
    assert store.keep(by_city["Vancouver"], no_other)


# --------------------------------------------------------------------------- database

needs_db = pytest.mark.skipif(
    os.environ.get("SCOUT_TEST_DB") != "1", reason="set SCOUT_TEST_DB=1 to run database tests"
)


@pytest.fixture
def conn():
    db.migrate(out=lambda _line: None)
    with db.connect("ingest") as c, c.transaction(force_rollback=True):
        yield c


def save(conn, by_term, fail=frozenset(), all_terms=True, cfg=CFG):
    result, records = sweep_of(by_term, fail)
    return store.save_sweep(conn, result, records, cfg, all_terms=all_terms)


def one(conn, sql, *params):
    return conn.execute(sql, params).fetchone()


def events(conn, workbc_id):
    return [
        k
        for (k,) in conn.execute(
            "SELECT e.kind FROM posting_events e JOIN postings p ON p.id = e.posting_id"
            " WHERE p.workbc_job_id = %s ORDER BY e.id",
            (workbc_id,),
        )
    ]


@needs_db
def test_first_sweep_inserts_everything(conn):
    s = save(conn, {"data scientist": [job(1), job(2)], "data analyst": [job(3, "Data Analyst")]})
    assert (s.new, s.changed, s.seen_again, s.removal_checked) == (3, 0, 0, True)
    assert s.employers_new == 1  # all three from the same employer
    row = one(
        conn,
        "SELECT area, match_quality, ats, ats_board, status, missed_runs, matched_terms"
        " FROM postings WHERE workbc_job_id = 't0001'",
    )
    assert row == ("metro", "exact", "ashby", "nw", "active", 0, ["data scientist"])
    assert events(conn, "t0001") == ["new"]
    assert one(conn, "SELECT count(*) FROM v_new_relevant_7d")[0] == 3
    run = one(conn, "SELECT ok, terms_ok, terms_failed, finished_at IS NOT NULL FROM scout_runs")
    assert run == (True, 2, 0, True)
    assert one(conn, "SELECT outcome, count FROM run_source_results") == ("ok", 3)


@needs_db
def test_rerun_with_same_data_adds_nothing(conn):
    """Acceptance test 2: idempotent."""
    data = {"data scientist": [job(1), job(2)], "data analyst": [job(3, "Data Analyst")]}
    save(conn, data)
    before = one(conn, "SELECT (SELECT count(*) FROM postings), (SELECT count(*) FROM employers),"
                       " (SELECT count(*) FROM posting_events)")  # fmt: skip
    s = save(conn, data)
    after = one(conn, "SELECT (SELECT count(*) FROM postings), (SELECT count(*) FROM employers),"
                      " (SELECT count(*) FROM posting_events)")  # fmt: skip
    assert (s.new, s.changed, s.seen_again, s.marked_missed) == (0, 0, 3, 0)
    assert before == after


@needs_db
def test_changed_title_is_an_event_with_old_and_new(conn):
    save(conn, {"data scientist": [job(1)]})
    s = save(conn, {"data scientist": [job(1, "Senior Data Scientist")]})
    assert s.changed == 1
    assert events(conn, "t0001") == ["new", "changed"]
    details = one(
        conn,
        "SELECT e.details FROM posting_events e JOIN postings p ON p.id = e.posting_id"
        " WHERE p.workbc_job_id = 't0001' AND e.kind = 'changed'",
    )[0]
    assert details == {"title": ["Data Scientist", "Senior Data Scientist"]}


@needs_db
def test_three_quick_misses_do_not_remove(conn):
    """Seen live: three sweeps in 5 minutes. Removal also needs 24 hours unseen."""
    save(conn, {"data scientist": [job(1), job(2)]})
    for _ in range(3):
        s = save(conn, {"data scientist": [job(1)]})
    assert s.marked_missed == 1 and s.removed == 0
    assert one(conn, "SELECT missed_runs, status FROM postings WHERE workbc_job_id = 't0002'") == (
        3,
        "active",
    )


@needs_db
def test_removed_after_three_complete_sweeps_and_can_reappear(conn, monkeypatch):
    monkeypatch.setattr(store, "UNSEEN_BEFORE_REMOVED", "0 seconds")  # the clock stands still here
    save(conn, {"data scientist": [job(1), job(2)]})
    for expected_misses in (1, 2):
        s = save(conn, {"data scientist": [job(1)]})
        assert s.marked_missed == 1 and s.removed == 0
        assert one(conn, "SELECT missed_runs, status FROM postings WHERE workbc_job_id = 't0002'") \
            == (expected_misses, "active")  # fmt: skip
    s = save(conn, {"data scientist": [job(1)]})
    assert s.removed == 1
    assert one(conn, "SELECT status FROM postings WHERE workbc_job_id = 't0002'") == ("removed",)

    s = save(conn, {"data scientist": [job(1), job(2)]})
    assert s.reappeared == 1
    assert one(conn, "SELECT status, missed_runs FROM postings WHERE workbc_job_id = 't0002'") \
        == ("active", 0)  # fmt: skip
    assert events(conn, "t0002") == ["new", "removed", "reappeared"]


@needs_db
def test_failed_term_marks_nothing_missed(conn):
    """Acceptance test 4: a sweep with a failed term never counts postings as missed."""
    save(conn, {"data scientist": [job(1)], "data analyst": [job(2, "Data Analyst")]})
    s = save(conn, {"data scientist": [job(1)]}, fail={"data analyst"})
    assert not s.removal_checked and s.marked_missed == 0
    assert one(conn, "SELECT missed_runs FROM postings WHERE workbc_job_id = 't0002'") == (0,)
    assert one(conn, "SELECT ok, terms_failed FROM scout_runs ORDER BY id DESC LIMIT 1") == (
        False,
        1,
    )
    assert one(conn, "SELECT outcome FROM run_source_results ORDER BY run_id DESC LIMIT 1") == (
        "transient",
    )


@needs_db
def test_term_subset_marks_nothing_and_keeps_terms(conn):
    shared = job(1, "Data Scientist and Data Analyst")
    save(conn, {"data scientist": [shared], "data analyst": [shared, job(2, "Data Analyst")]})
    s = save(conn, {"data scientist": [shared]}, all_terms=False)
    assert not s.removal_checked and s.marked_missed == 0
    terms = one(conn, "SELECT matched_terms FROM postings WHERE workbc_job_id = 't0001'")[0]
    assert terms == ["data scientist", "data analyst"]


@needs_db
def test_all_empty_sweep_is_structural_and_marks_nothing(conn):
    save(conn, {"data scientist": [job(1)]})
    s = save(conn, {"data scientist": [], "data analyst": []})
    assert not s.removal_checked
    assert one(conn, "SELECT outcome FROM run_source_results ORDER BY run_id DESC LIMIT 1") == (
        "structural",
    )


@needs_db
def test_same_board_posting_twice_on_workbc_is_allowed(conn):
    """Migration 0002: WorkBC may list one ATS posting under two JobIds."""
    a = job(1)
    b = job(2)
    b["ExternalSource"] = a["ExternalSource"]
    s = save(conn, {"data scientist": [a, b]})
    assert s.new == 2


@needs_db
def test_source_degrades_after_three_failures(conn):
    for _ in range(3):
        save(conn, {"data scientist": []}, fail={"data analyst"})
    row = one(conn, "SELECT status, consecutive_failures FROM sources WHERE name = 'workbc'")
    assert row == ("degraded", 3)
    save(conn, {"data scientist": [job(1)], "data analyst": [job(2, "Data Analyst")]})
    row = one(conn, "SELECT status, consecutive_failures FROM sources WHERE name = 'workbc'")
    assert row == ("active", 0)


@needs_db
def test_out_of_area_postings_skipped_when_configured(conn):
    cfg = dataclasses.replace(CFG, area=dataclasses.replace(CFG.area, keep_other_bc=False))
    s = save(conn, {"data scientist": [job(1), job(2, city="Victoria")]}, cfg=cfg)
    assert (s.new, s.skipped_area) == (1, 1)


@pytest.fixture
def clean_db():
    """For tests that commit: empty Scout's tables afterwards (as scout_migrate, the owner)."""
    db.migrate(out=lambda _line: None)
    yield
    with db.connect("migrate", autocommit=True) as c:
        c.execute(
            "TRUNCATE posting_events, run_source_results, postings, employers, scout_runs,"
            " sources RESTART IDENTITY"
        )


@needs_db
def test_scout_sweep_command_saves_and_reruns_cleanly(clean_db):
    """End to end through `scout sweep` (not --dry-run), committed, twice."""
    from scout import sweep

    page = json.dumps({"result": [job(1), job(2, city="Burnaby")], "count": 2}).encode()
    empty = json.dumps({"result": [], "count": 0}).encode()

    def post(url, body):
        return page if body["Keyword"] == "data scientist" else empty

    first: list[str] = []
    assert (
        sweep.run(
            False,
            out=first.append,
            post=post,
            sleep=lambda s: None,
            save_raw=False,
            enrich_after=False,
        )
        == 0
    )
    assert "WorkBC sweep (saving to the database)" in first[0]
    assert any("new 2, changed 0, seen again 0" in line for line in first)

    second: list[str] = []
    assert (
        sweep.run(
            False,
            out=second.append,
            post=post,
            sleep=lambda s: None,
            save_raw=False,
            enrich_after=False,
        )
        == 0
    )
    assert any("new 0, changed 0, seen again 2" in line for line in second)
    assert any("not seen this time: 0" in line for line in second)

    with db.connect("reader") as c:
        assert c.execute("SELECT count(*) FROM postings").fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM scout_runs WHERE ok").fetchone()[0] == 2


@needs_db
def test_duplicates_are_grouped_and_stable(conn):
    """Task 1.7: the Activision case lands in one group; a rerun changes nothing."""

    def act(n, employer, url):
        j = job(n, "Data Scientist, Anti-Cheat", employer=employer)
        j["ExternalSource"] = {"Source": [{"Source": "x", "Url": url}]}
        return j

    data = {
        "data scientist": [
            act(1, "Activision Blizzard", "https://careers.activisionblizzard.com/job/ACCOUSR028031EXTERNAL/DS"),
            act(2, "Activision Blizzard, Inc.", "https://games.wd1.myworkdayjobs.com/E/job/SO/DS_R028031-1"),
            act(3, "Activision", "https://careers.activision.com/job/ACPUUSR028031EXTERNAL/DS"),
            job(4),
        ]
    }  # fmt: skip
    s = save(conn, data)
    assert (s.dup_groups, s.dup_postings, s.dup_changed) == (1, 3, 3)
    rows = conn.execute("SELECT id, dup_group_id FROM v_duplicates").fetchall()
    assert len(rows) == 3 and len({g for _, g in rows}) == 1
    assert rows[0][1] == min(i for i, _ in rows)
    assert one(conn, "SELECT dup_group_id FROM postings WHERE workbc_job_id = 't0004'") == (None,)

    s = save(conn, data)
    assert (s.dup_groups, s.dup_changed) == (1, 0)


@needs_db
def test_scout_status_summarizes(clean_db):
    from scout import status, sweep

    page = json.dumps({"result": [job(1), job(2, "Data Analyst", city="British Columbia")],
                       "count": 2}).encode()  # fmt: skip
    empty = json.dumps({"result": [], "count": 0}).encode()
    sweep.run(
        False,
        out=lambda _l: None,
        sleep=lambda s: None,
        save_raw=False,
        enrich_after=False,
        post=lambda url, body: page if body["Keyword"] == "data scientist" else empty,
    )
    lines: list[str] = []
    assert status.run(out=lines.append) == 0
    text = "\n".join(lines)
    assert "active 2 (WorkBC 2, found on job boards 0); removed 0" in text
    assert "relevant now (Metro Vancouver or remote, strong title match): 2" in text
    assert "by area: " in text and "metro 1" in text and "remote 1" in text
    assert "== recent runs" in text and "sweep  ok" in text
    assert "== sources needing attention: none" in text
    assert "== newest relevant jobs" in text and "Data Scientist | Northwind Analytics Inc." in text
