"""Tests for the daily digest (task 2.3). Database tests run with SCOUT_TEST_DB=1."""

from __future__ import annotations

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
