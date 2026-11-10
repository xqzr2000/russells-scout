"""Tests for the daily digest (task 2.3). Database tests run with SCOUT_TEST_DB=1."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from scout import db, digest
from scout.digest import CLOSED, NEW, POSSIBLE, SUSPICIOUS, Digest, Job, Section

# --------------------------------------------------------------------------- rendering


def sample_digest(problems=None) -> Digest:
    now = datetime(2026, 11, 1, 14, 10, tzinfo=UTC)
    new = [
        Job("AI Engineer", "Remarcable, Inc.", "Vancouver (hybrid)",
            "https://jobs.ashbyhq.com/remarcable-inc/x", pay="CAD 120,000–150,000"),
        Job("Data <Scientist> & co", "A&B", "Burnaby", None),
    ]  # fmt: skip
    return Digest(
        covers_from=now - timedelta(days=1), covers_to=now, day="Sun Nov 1", stamp="2026-11-01-0710",
        since="Sat Oct 31 07:10", first=False, relevant_open=150,
        sections=[
            Section(NEW, "strong", new, 30),
            Section(POSSIBLE, "loose", [], 0),
            Section(CLOSED, "gone", [], 0),
            Section(SUSPICIOUS, "flags", [], 0),
        ],
        health=["sweeps: 2 of 2 ok"], problems=problems or [],
    )  # fmt: skip


def test_title_counts():
    assert sample_digest().title == "Scout: 30 new jobs, 0 suspicious (Sun Nov 1)"
    d = sample_digest(problems=["x"])
    assert d.title == "Scout: 30 new jobs, 0 suspicious, 1 problem (Sun Nov 1)"
    d.sections[0].total = 0
    assert d.title.startswith("Scout: no new jobs,")


def test_text_lists_jobs_links_and_more():
    t = sample_digest(problems=["The last backup failed: x"]).text()
    assert t.startswith("Scout: 30 new jobs, 0 suspicious, 1 problem (Sun Nov 1)\n\n")
    assert "since Sat Oct 31 07:10" in t
    assert "NEEDS YOUR ATTENTION\n  ! The last backup failed: x" in t
    assert "AI Engineer — Remarcable, Inc. — Vancouver (hybrid) — CAD 120,000–150,000" in t
    assert "    https://jobs.ashbyhq.com/remarcable-inc/x" in t
    assert "… and 28 more" in t
    assert "NEW POSSIBLE MATCHES (0)" in t and "  none" in t


def test_html_escapes_and_links():
    h = sample_digest().html()
    assert '<a href="https://jobs.ashbyhq.com/remarcable-inc/x">AI Engineer</a>' in h
    assert "Data &lt;Scientist&gt; &amp; co" in h and "A&amp;B" in h
    assert "<Scientist>" not in h
    assert '<meta charset="utf-8">' in h


def test_files_written_and_latest_replaced(tmp_path):
    d = sample_digest()
    path = digest.write_files(d, tmp_path)
    assert path.name == "2026-11-01-0710.txt"
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["2026-11-01-0710.html", "2026-11-01-0710.txt", "latest.html", "latest.txt"]
    assert (tmp_path / "latest.txt").read_text() == path.read_text() == d.text()
    assert oct(path.stat().st_mode & 0o777) == "0o644"
    d.stamp, d.day = "2026-11-02-0710", "Mon Nov 2"
    digest.write_files(d, tmp_path)
    assert "(Mon Nov 2)" in (tmp_path / "latest.txt").read_text()
    assert len(list(tmp_path.glob("2026-*.txt"))) == 2


def test_missing_folder_is_explained(tmp_path):
    with pytest.raises(digest.DigestError, match="doesn't exist"):
        digest.write_files(sample_digest(), tmp_path / "nope")


def test_prune_keeps_90_days_and_other_files(tmp_path):
    for name in ("2026-07-01-0710.txt", "2026-07-01-0710.html", "2026-08-15-0710.txt",
                 "latest.txt", "notes.txt", "2026-07-01-0710.md"):  # fmt: skip
        (tmp_path / name).write_text("x")
    removed = digest.prune(tmp_path, datetime(2026, 11, 1, tzinfo=UTC))
    assert removed == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "2026-07-01-0710.md",
        "2026-08-15-0710.txt",
        "latest.txt",
        "notes.txt",
    ]


# --------------------------------------------------------------------------- health files


def agent_logs(tmp_path, entries):
    (tmp_path / "logs" / "agent").mkdir(parents=True, exist_ok=True)
    lines = [e if isinstance(e, str) else json.dumps(e) for e in entries]
    (tmp_path / "logs" / "agent" / "sessions.log").write_text("\n".join(lines) + "\n")
    return tmp_path / "logs"


def agent_health(logs, outbox, frm, to):
    health, problems = [], []
    digest._agent_health(logs, outbox, frm, to, health, problems)
    return health, problems


AGENT_TO = datetime(2026, 11, 9, 14, 10, tzinfo=UTC)  # 07:10 Vancouver
AGENT_FROM = AGENT_TO - timedelta(days=1)


def test_agent_health_without_sessions_is_quiet(tmp_path):
    health, problems = agent_health(tmp_path, tmp_path / "none", AGENT_FROM, AGENT_TO)
    assert health == ["agent: no sessions yet"] and problems == []


def test_agent_health_counts_sessions_and_flags_bad_ones(tmp_path):
    logs = agent_logs(tmp_path, [
        {"time": "2026-11-07T10:21:00-0700", "outcome": "turn_limit", "task": "0001"},  # older
        "not json",
        {"time": "2026-11-08T10:21:00-0700", "outcome": "skipped", "task": None},
        {"time": "2026-11-08T12:00:00-0700", "outcome": "timeout", "task": "0002",
         "detail": "hit the 1800s time limit"},
        {"time": "2026-11-08T13:00:00-0700", "outcome": "ran", "task": "0002", "turns": 41},
    ])  # fmt: skip
    health, problems = agent_health(logs, tmp_path / "none", AGENT_FROM, AGENT_TO)
    assert health == [
        "agent: 3 check(s) since the last digest (ran 1, skipped 1, timeout 1);"
        " last: ran 18 hours ago, task 0002, 41 turns"
    ]
    assert len(problems) == 1
    assert "ended with timeout" in problems[0] and "task 0002" in problems[0]
    assert "1800s" in problems[0]


def test_agent_health_notices_a_stopped_timer(tmp_path):
    logs = agent_logs(tmp_path, [{"time": "2026-11-06T10:21:00-0700", "outcome": "skipped"}])
    health, problems = agent_health(logs, tmp_path / "none", AGENT_FROM, AGENT_TO)
    assert health[0].startswith("agent: 0 check(s) since the last digest; last: skipped")
    assert len(problems) == 1 and "scout-agent.timer" in problems[0]


def test_agent_health_reads_the_outbox(tmp_path):
    logs = agent_logs(tmp_path, [{"time": "2026-11-08T10:21:00-0700", "outcome": "ran"}])
    out = tmp_path / "outbox"
    (out / "notes").mkdir(parents=True)
    (out / "manifest.txt").write_text(
        "time=2026-11-08T17:30:00+00:00\norigin_main=24b1d28\n"
        "branch=agent/0001-workday 0cde13b 2026-11-08T04:54:02+00:00 Older work\n"
        "branch=agent/0002-amazon 1a2b3c4 2026-11-08T17:25:00+00:00 Read Amazon jobs\n"
        "branch=agent/0003-merged 5d6e7f8 2026-11-08T17:20:00+00:00 Already reviewed\n"
        "pending=agent/0002-amazon\n"
    )
    (out / "notes" / "handoff.md").write_text(
        "rules: scout-rules-2\n\n## Done\n\n- things\n\n## Needs your attention\n\n"
        "- The <b>site</b> needs a new column:\n  posting_type.\n- Second point\n\n## Next\n"
    )
    (out / "guard.log").write_text(
        '{"time":"2026-11-08T17:00:00Z","reason":"x"}\n'
        '{"time":"2026-11-01T17:00:00Z","reason":"old"}\nbroken\n'
    )
    health, problems = agent_health(logs, out, AGENT_FROM, AGENT_TO)
    assert "agent: the guard blocked 1 action(s) since the last digest" in health
    assert "agent asks: The <b>site</b> needs a new column: posting_type." in health
    assert "agent asks: Second point" in health
    # Only pending branches (work beyond main, task not done) ask for review.
    assert len(problems) == 1 and "agent/0002-amazon (Read Amazon jobs)" in problems[0]
    assert "agent.bundle" in problems[0]

    # An old handoff (not changed since the last digest) isn't repeated.
    old = (AGENT_FROM - timedelta(hours=1)).timestamp()
    os.utime(out / "notes" / "handoff.md", (old, old))
    health, _ = agent_health(logs, out, AGENT_FROM, AGENT_TO)
    assert not any(h.startswith("agent asks") for h in health)


def test_handoff_nothing_and_long_lines(tmp_path):
    f = tmp_path / "h.md"
    f.write_text("## Needs your attention\n\n- nothing\n")
    assert digest._handoff_asks(f) == []
    f.write_text("## Needs your attention\n\n- " + "x" * 500 + "\n")
    [ask] = digest._handoff_asks(f)
    assert len(ask) == digest.AGENT_LINE_MAX and ask.endswith("…")


def test_agent_lines_are_escaped_in_html():
    d = sample_digest(problems=["Agent work to review: agent/x (<script>)"])
    d.health.append("agent asks: <img src=x>")
    page = d.html()
    assert "<script>" not in page and "<img" not in page
    assert "&lt;script&gt;" in page and "&lt;img src=x&gt;" in page


def test_backup_and_sync_health(tmp_path):
    now = datetime(2026, 11, 1, 14, 0, tzinfo=UTC)
    health: list[str] = []
    problems: list[str] = []
    digest._backup_health(tmp_path, now, health, problems)
    digest._sync_health(tmp_path, now, health, problems)
    assert health == ["backup: no backup recorded yet", "sync to WSL2: not set up yet"]
    assert len(problems) == 1

    (tmp_path / "backup-status").write_text(
        "time=2026-11-01T03:30:12-07:00\nresult=ok\nfile=f.dump\nbytes=173900\n"
        "message=restore check ok\n"
    )
    (tmp_path / "sync").mkdir()
    (tmp_path / "sync" / "last-sync").write_text("2026-10-29T10:00:00+00:00\n")
    health, problems = [], []
    digest._backup_health(tmp_path, now, health, problems)
    digest._sync_health(tmp_path, now, health, problems)
    assert health == ["backup: 3 hours ago, 0.2 MB, restore check ok", "sync to WSL2: 3 days ago"]
    assert problems == ["WSL2 hasn't pulled the backups for 3 days."]

    (tmp_path / "backup-status").write_text(
        "time=2026-11-01T03:30:12-07:00\nresult=failed\nmessage=pg_dump failed\n"
    )
    health, problems = [], []
    digest._backup_health(tmp_path, now, health, problems)
    assert problems == ["The last backup failed: pg_dump failed"]


# --------------------------------------------------------------------------- with the database

needs_db = pytest.mark.skipif(
    os.environ.get("SCOUT_TEST_DB") != "1", reason="set SCOUT_TEST_DB=1 to run database tests"
)
TABLES = "posting_events, run_source_results, postings, employers, scout_runs, sources, digests"


@pytest.fixture
def clean_db():
    db.migrate(out=lambda _line: None)
    with db.connect("migrate", autocommit=True) as c:
        c.execute(f"TRUNCATE {TABLES} RESTART IDENTITY")
    yield
    with db.connect("migrate", autocommit=True) as c:
        c.execute(f"TRUNCATE {TABLES} RESTART IDENTITY")


def add_posting(c, title, hours_ago, area="metro", quality="exact", status="active",
                trust=2, reasons=(), jid=None, jump=None):  # fmt: skip
    return c.execute(
        "INSERT INTO postings (found_via, workbc_job_id, title, employer_name, city, area,"
        " match_quality, status, trust_score, trust_reasons, jump_url, first_seen_at)"
        " VALUES ('workbc', %s, %s, 'Acme', 'Vancouver', %s, %s, %s, %s, %s, %s,"
        " now() - make_interval(hours => %s)) RETURNING id",
        (jid, title, area, quality, status, trust, list(reasons), jump, hours_ago),
    ).fetchone()[0]


@pytest.fixture
def data(clean_db):
    with db.connect("ingest", autocommit=True) as c:
        add_posting(c, "AI Engineer", 1, jump="https://jobs.lever.co/acme/1")
        add_posting(c, "Data Scientist", 2, jid="48123456")
        add_posting(c, "Old Data Engineer", 30)  # before the first digest's 24 hours
        add_posting(c, "Data & Systems Analyst", 3, quality="loose")
        add_posting(c, "Data Analyst", 3, area="other_bc")
        add_posting(c, "Data Entry Clerk", 4, trust=-5, reasons=["fee_requested"])
        gone = add_posting(c, "ML Engineer", 50, status="removed")
        c.execute("INSERT INTO posting_events (posting_id, kind) VALUES (%s, 'removed')", (gone,))
        c.execute(
            "INSERT INTO scout_runs (kind, started_at, finished_at, ok)"
            " VALUES ('sweep', now() - interval '2 hours', now() - interval '2 hours', true)"
        )


@needs_db
def test_first_digest_covers_the_last_24_hours(data, tmp_path):
    with db.connect("reader") as c:
        d = digest.gather(c, logs_dir=tmp_path)
    assert d.first
    assert [j.title for j in d.sections[0].jobs] == ["AI Engineer", "Data Scientist"]
    assert d.sections[0].jobs[0].link == "https://jobs.lever.co/acme/1"
    assert d.sections[0].jobs[1].link.endswith("#/job-details/48123456")
    assert d.count(POSSIBLE) == 1 and d.count(CLOSED) == 1 and d.count(SUSPICIOUS) == 1
    assert d.sections[3].jobs[0].note == "-5: fee_requested"
    assert d.relevant_open == 3  # includes the 30-hour-old one
    assert d.health[0].startswith("sweeps: 1 of 1 ok in the last 24 hours; last good sweep")
    # No backup recorded in tmp_path: that's the only problem.
    assert d.problems == ["No database backup has been recorded (ops/backup.sh, box-setup §16)."]


@needs_db
def test_a_job_seen_on_its_board_first_isnt_new_again_from_workbc(data, tmp_path):
    # 2026-11-10: Workday jobs found on the company board at 16:49, then on WorkBC the next
    # morning, were listed as new twice. Both postings share a duplicate group.
    with db.connect("ingest", autocommit=True) as c:
        board = add_posting(c, "Data Analyst 1", 30, jump="https://pfg.example/job/R-1")
        workbc = add_posting(c, "Data Analyst 1", 1, jid="48999999")
        c.execute("UPDATE postings SET dup_group_id = %s WHERE id IN (%s, %s)",
                  (board, board, workbc))  # fmt: skip
        # A group whose first posting is inside the window is still listed once.
        a = add_posting(c, "Applied Scientist", 5, jump="https://board.example/7")
        b = add_posting(c, "Applied Scientist", 4, jid="48999998")
        c.execute("UPDATE postings SET dup_group_id = %s WHERE id IN (%s, %s)", (a, a, b))
    with db.connect("reader") as c:
        d = digest.gather(c, logs_dir=tmp_path)
    titles = [j.title for j in d.sections[0].jobs]
    assert "Data Analyst 1" not in titles
    assert titles.count("Applied Scientist") == 1
    assert d.sections[0].jobs[titles.index("Applied Scientist")].link == "https://board.example/7"
    assert d.relevant_open == 3 + 2  # the fixture's 3, plus one per new group


@needs_db
def test_digest_is_written_recorded_and_the_next_starts_after_it(data, tmp_path):
    logs, out_dir = tmp_path / "logs", tmp_path / "digests"
    logs.mkdir()
    out_dir.mkdir()
    lines: list[str] = []
    assert digest.run(out=lines.append, logs_dir=logs, digests_dir=out_dir) == 0, lines
    assert lines[0].startswith("Scout: 2 new jobs, 1 suspicious, 1 problem (")
    first = (out_dir / "latest.txt").read_text()
    assert "AI Engineer — Acme" in first
    with db.connect("reader") as c:
        rows = c.execute(
            "SELECT new_relevant, new_possible, closed, suspicious, problems, file FROM digests"
        ).fetchall()
    assert rows[0][:5] == (2, 1, 1, 1, 1) and rows[0][5].endswith(".txt")
    assert (out_dir / rows[0][5]).exists()

    with db.connect("ingest", autocommit=True) as c:
        add_posting(c, "Machine Learning Engineer", 0)
    assert digest.run(out=lines.append, logs_dir=logs, digests_dir=out_dir) == 0
    second = (out_dir / "latest.txt").read_text()
    assert second.startswith("Scout: 1 new job, 0 suspicious,")
    assert "Machine Learning Engineer" in second and "AI Engineer —" not in second


@needs_db
def test_failed_write_records_nothing(data, tmp_path):
    lines: list[str] = []
    assert digest.run(out=lines.append, logs_dir=tmp_path, digests_dir=tmp_path / "nope") == 1
    assert "Nothing recorded" in lines[-1]
    with db.connect("reader") as c:
        assert c.execute("SELECT count(*) FROM digests").fetchone()[0] == 0


@needs_db
def test_dry_run_prints_and_records_nothing(data, tmp_path):
    lines: list[str] = []
    assert digest.run(dry_run=True, out=lines.append, logs_dir=tmp_path, digests_dir=tmp_path) == 0
    assert lines[0].startswith("Scout: 2 new jobs")
    assert lines[-1] == "(dry run: nothing written, nothing recorded)"
    assert list(tmp_path.iterdir()) == []
    with db.connect("reader") as c:
        assert c.execute("SELECT count(*) FROM digests").fetchone()[0] == 0


@needs_db
def test_no_good_sweep_is_a_problem(clean_db, tmp_path):
    with db.connect("ingest", autocommit=True) as c:
        c.execute(
            "INSERT INTO scout_runs (kind, started_at, finished_at, ok)"
            " VALUES ('sweep', now() - interval '30 hours', now() - interval '30 hours', true),"
            "        ('sweep', now() - interval '3 hours', now() - interval '3 hours', false)"
        )
    with db.connect("reader") as c:
        d = digest.gather(c, logs_dir=tmp_path)
    assert d.health[0].startswith("sweeps: 0 of 1 ok in the last 24 hours")
    assert any(p.startswith("No successful sweep in the last 24 hours") for p in d.problems)
    assert d.title.startswith("Scout: no new jobs, 0 suspicious, 2 problems")
