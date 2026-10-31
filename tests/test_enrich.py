"""Tests for job-board enrichment (task 1.8). Database tests run with SCOUT_TEST_DB=1 and are
rolled back. Boards are the synthetic ones in tests/fixtures/ats/."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scout import config, db, enrich, store
from scout.pipeline import classify
from scout.pipeline.record import build
from scout.sources import ats, workbc
from scout.sources.ats import BoardPosting
from scout.sources.workbc import HttpError

REPO = Path(__file__).resolve().parents[1]
CFG = config.load(REPO / "config" / "search.json")
FIX = Path(__file__).parent / "fixtures" / "ats"

ASHBY_URL = ats.ASHBY.format(board="northwind")
GH_URL = ats.GREENHOUSE.format(board="contoso")
LEVER_URL = ats.LEVER.format(board="fabrikam")
LEVER_EU_URL = ats.LEVER_EU.format(board="fabrikam")
ASANA_URL = ats.GREENHOUSE.format(board="asana")
ASANA_JOB_URL = ats.GREENHOUSE_JOB.format(board="asana", job_id="8275149")
ASANA_BOARD = json.dumps(
    {"jobs": [{"id": 8275149, "title": "Data Scientist", "absolute_url": "https://x",
               "location": {"name": "Vancouver"}, "content": "&lt;p&gt;Hi&lt;/p&gt;"}]}
).encode()  # fmt: skip


def boards(**overrides):
    base = {
        ASHBY_URL: (FIX / "ashby_northwind.json").read_bytes(),
        GH_URL: (FIX / "greenhouse_contoso.json").read_bytes(),
        LEVER_EU_URL: (FIX / "lever_fabrikam.json").read_bytes(),  # global host: 404
        ASANA_URL: ASANA_BOARD,
        ASANA_JOB_URL: json.dumps({"id": 8275149, "title": "Data Scientist"}).encode(),
    }
    base.update(overrides)

    def get(url):
        body = base.get(url)
        if body is None:
            raise HttpError(404)
        if isinstance(body, Exception):
            raise body
        return body

    return get


def wjob(n, title, employer, url, city="Vancouver"):
    return {"JobId": f"w{n}", "Title": title, "City": city, "EmployerName": employer,
            "ExternalSource": {"Source": [{"Source": "x", "Url": url}]}}  # fmt: skip


WORKBC = [
    wjob(1, "Senior Data Scientist", "Northwind",
         "https://jobs.ashbyhq.com/northwind/11111111-2222-3333-4444-555555555555"),
    wjob(2, "Sr. Data Scientist, Pricing", "Contoso",
         "https://job-boards.greenhouse.io/contoso/jobs/8525610002"),
    wjob(3, "Data Engineer", "Fabrikam",
         "https://jobs.lever.co/fabrikam/abcdef12-3456-7890-abcd-ef1234567890"),
    wjob(4, "Data Scientist", "Asana", "https://www.asana.com/jobs/apply/8275149?gh_jid=8275149"),
]  # fmt: skip


# --------------------------------------------------------------------------- unit


@pytest.mark.parametrize(
    ("location", "city", "country", "workplace", "area"),
    [
        ("Vancouver, BC", "Vancouver", "Canada", "hybrid", "metro"),
        ("Burnaby, BC", "Burnaby", "CA", "onsite", "metro"),
        ("Remote (Canada)", "Remote", "Canada", "remote", "remote"),
        ("Remote - Canada", "Remote", None, "remote", "remote"),
        ("Remote", "Remote", None, "remote", "remote"),
        ("Remote (US)", "Remote", "United States", "remote", "outside"),
        ("Remote - EMEA", "Remote", None, "remote", "outside"),
        ("Remote", "Remote", "US", "remote", "outside"),
        ("Seattle, WA", "Seattle", "United States", "onsite", "outside"),
        ("Toronto, Ontario, Canada", "Toronto", None, "unknown", "outside"),
    ],
)
def test_ats_area(location, city, country, workplace, area):
    bp = BoardPosting("ashby", "b", "1", "Data Scientist", None, location, city, country, workplace)
    assert enrich.ats_area(bp, CFG.area.metro) == area


def test_term_hits_like_workbc_search():
    assert classify.term_hits("Engineer, Data Platform", CFG.terms) == ("data engineer",)
    assert classify.term_hits("Office Manager", CFG.terms) == ()
    assert "ML engineer" in classify.term_hits("Senior Machine Learning Engineer", CFG.terms)


# --------------------------------------------------------------------------- database

needs_db = pytest.mark.skipif(
    os.environ.get("SCOUT_TEST_DB") != "1", reason="set SCOUT_TEST_DB=1 to run database tests"
)


@pytest.fixture
def conn():
    db.migrate(out=lambda _line: None)
    with db.connect("ingest", autocommit=True) as c, c.transaction(force_rollback=True):
        yield c


def sweep_save(conn, jobs=WORKBC):
    def post(url, body):
        if body["Keyword"] == "data scientist":
            return json.dumps({"result": jobs, "count": len(jobs)}).encode()
        return json.dumps({"result": [], "count": 0}).encode()

    result = workbc.sweep(["data scientist", "data engineer"], page_size=50, max_pages=2,
                          delay=0.5, post=post, sleep=lambda s: None)  # fmt: skip
    records = [build(j, result.matched_terms[i], CFG) for i, j in result.jobs.items()]
    return store.save_sweep(conn, result, records, CFG, all_terms=False)


def run(conn, get=None, **kw):
    return enrich.enrich(conn, CFG, get=get or boards(), sleep=lambda s: None, **kw)


def one(conn, sql, *params):
    return conn.execute(sql, params).fetchone()


@needs_db
def test_enrichment_matches_adds_and_discovers(conn):
    sweep_save(conn)
    s = run(conn)

    assert s.discovered == [("Asana", "asana")]
    by_board = {f"{b.ats}:{b.board}": b for b in s.boards}
    assert set(by_board) == {"ashby:northwind", "greenhouse:contoso", "lever:fabrikam",
                             "greenhouse:asana"}  # fmt: skip
    assert all(b.outcome == "ok" for b in s.boards)

    # WorkBC postings got the board's details
    row = one(conn, "SELECT description, pay_min, pay_period, ats_workplace, enriched_at IS NOT NULL"
                    " FROM postings WHERE workbc_job_id = 'w1'")  # fmt: skip
    assert row == ("Build risk models. Python and SQL.", 140000, "year", "hybrid", True)
    assert one(conn, "SELECT workplace FROM v_new_relevant_7d WHERE title = 'Senior Data Scientist'"
               ) == ("hybrid",)  # fmt: skip
    assert one(conn, "SELECT ats_board, enriched_at IS NOT NULL FROM postings"
                     " WHERE workbc_job_id = 'w4'") == ("asana", True)  # fmt: skip
    assert one(conn, "SELECT ats, ats_board FROM employers WHERE name_norm = 'asana'") \
        == ("greenhouse", "asana")  # fmt: skip

    # board-only postings that match a term, in Metro Vancouver or remote in Canada
    added = {
        r[0]: r[1:]
        for r in conn.execute(
            "SELECT title, area, ats, match_quality, workbc_job_id FROM postings"
            " WHERE found_via = 'ats'"
        )
    }
    assert added == {
        "Machine Learning Engineer": ("remote", "ashby", "exact", None),
        "Data Analyst": ("remote", "greenhouse", "exact", None),
        "Quantitative Analyst": ("metro", "lever", "exact", None),
    }
    assert sum(b.new for b in s.boards) == 3
    assert sum(b.matched for b in s.boards) == 4


@needs_db
def test_rerun_and_sweep_keep_everything_stable(conn):
    sweep_save(conn)
    run(conn)
    events = one(conn, "SELECT count(*) FROM posting_events")[0]
    s = run(conn)
    assert s.discovered == []  # board already known: no probing
    assert sum(b.new for b in s.boards) == 0 and sum(b.seen_again for b in s.boards) == 3
    assert one(conn, "SELECT count(*) FROM posting_events")[0] == events
    sweep_save(conn)  # the WorkBC link names no board: the discovered one must stay
    assert one(conn, "SELECT ats_board FROM postings WHERE workbc_job_id = 'w4'") == ("asana",)


@needs_db
def test_failing_board_does_not_stop_the_others(conn):
    sweep_save(conn)
    s = run(conn, get=boards(**{ASHBY_URL: HttpError(503)}))
    by_board = {f"{b.ats}:{b.board}": b for b in s.boards}
    assert by_board["ashby:northwind"].outcome == "transient"
    assert by_board["greenhouse:contoso"].matched == 1
    assert one(conn, "SELECT description FROM postings WHERE workbc_job_id = 'w1'") == (None,)
    assert one(conn, "SELECT ok, terms_failed FROM scout_runs WHERE kind = 'enrich'") == (False, 1)
    assert one(
        conn,
        "SELECT r.outcome FROM run_source_results r JOIN sources s ON s.id = r.source_id"
        " WHERE s.name = 'ashby:northwind'",
    ) == ("transient",)


@needs_db
def test_board_postings_removed_after_three_misses(conn):
    sweep_save(conn)
    run(conn)
    ashby = json.loads((FIX / "ashby_northwind.json").read_text())
    ashby["jobs"] = [j for j in ashby["jobs"] if j["title"] != "Machine Learning Engineer"]
    gone = boards(**{ASHBY_URL: json.dumps(ashby).encode()})
    for _ in range(2):
        run(conn, get=gone)
    assert one(conn, "SELECT status, missed_runs FROM postings"
                     " WHERE title = 'Machine Learning Engineer'") == ("active", 2)  # fmt: skip
    s = run(conn, get=gone)
    assert sum(b.removed for b in s.boards) == 1
    assert one(conn, "SELECT status FROM postings WHERE title = 'Machine Learning Engineer'") \
        == ("removed",)  # fmt: skip
    # a failing board marks nothing
    run(conn, get=boards(**{GH_URL: HttpError(500)}))
    assert one(conn, "SELECT missed_runs FROM postings WHERE title = 'Data Analyst'") == (0,)


@needs_db
def test_dry_run_writes_nothing(conn):
    sweep_save(conn)
    before = one(conn, "SELECT (SELECT count(*) FROM postings), (SELECT count(*) FROM scout_runs),"
                       " (SELECT count(*) FROM postings WHERE enriched_at IS NOT NULL)")  # fmt: skip
    s = run(conn, dry_run=True)
    after = one(conn, "SELECT (SELECT count(*) FROM postings), (SELECT count(*) FROM scout_runs),"
                      " (SELECT count(*) FROM postings WHERE enriched_at IS NOT NULL)")  # fmt: skip
    assert before == after
    assert s.discovered == [("Asana", "asana")]
    assert len([c for b in s.boards for c in b.candidates]) == 3
    assert one(conn, "SELECT ats_board FROM postings WHERE workbc_job_id = 'w4'") == (None,)


@needs_db
def test_only_one_board(conn):
    sweep_save(conn)
    s = run(conn, only=["lever:fabrikam"])
    assert [f"{b.ats}:{b.board}" for b in s.boards] == ["lever:fabrikam"]


@pytest.fixture
def clean_db():
    db.migrate(out=lambda _line: None)
    yield
    with db.connect("migrate", autocommit=True) as c:
        c.execute(
            "TRUNCATE posting_events, run_source_results, postings, employers, scout_runs,"
            " sources RESTART IDENTITY"
        )


@needs_db
def test_scout_sweep_saves_then_enriches(clean_db):
    """End to end through `scout sweep`, committed: save, then the job boards."""
    from scout import sweep

    def post(url, body):
        jobs = WORKBC if body["Keyword"] == "data scientist" else []
        return json.dumps({"result": jobs, "count": len(jobs)}).encode()

    lines: list[str] = []
    code = sweep.run(False, out=lines.append, post=post, sleep=lambda s: None, save_raw=False,
                     ats_get=boards())  # fmt: skip
    text = "\n".join(lines)
    assert code == 0, text
    assert "new 4, changed 0" in text
    assert "Greenhouse boards found from company links: asana (Asana)" in text
    assert "WorkBC postings matched on their boards: 4" in text
    assert "from boards: new 3" in text
    with db.connect("reader") as c:
        assert c.execute("SELECT count(*) FROM v_new_relevant_7d").fetchone()[0] == 7
