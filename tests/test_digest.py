"""Tests for the daily digest (tasks 2.3–2.4). Database tests run with SCOUT_TEST_DB=1.

No real email is sent: a fake SMTP class records what would have gone out.
"""

from __future__ import annotations

import os
import smtplib
import typing
from datetime import UTC, datetime, timedelta

import pytest

from scout import db, digest
from scout.digest import CLOSED, NEW, POSSIBLE, SUSPICIOUS, Digest, Job, MailSettings, Section

MAIL_ENV = {
    "SCOUT_SMTP_USER": "russell.scout@gmail.com",
    "SCOUT_SMTP_PASSWORD": "abcd efgh ijkl mnop",
    "SCOUT_MAIL_TO": "russell@example.com",
}


class FakeSMTP:
    """Stands in for smtplib.SMTP; remembers every instance."""

    sent: typing.ClassVar[list] = []
    calls: typing.ClassVar[list] = []
    fail_login = False
    fail_send = False

    def __init__(self, host, port, timeout=None, context=None):
        FakeSMTP.calls.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        FakeSMTP.calls.append(("quit",))

    def ehlo(self):
        FakeSMTP.calls.append(("ehlo",))

    def starttls(self, context=None):
        FakeSMTP.calls.append(("starttls",))

    def login(self, user, password):
        FakeSMTP.calls.append(("login", user, password))
        if FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")

    def send_message(self, msg, from_addr=None, to_addrs=None):
        if FakeSMTP.fail_send:
            raise smtplib.SMTPServerDisconnected("connection closed")
        FakeSMTP.calls.append(("send", from_addr, tuple(to_addrs)))
        FakeSMTP.sent.append(msg)


@pytest.fixture(autouse=True)
def fresh_fake():
    FakeSMTP.sent, FakeSMTP.calls = [], []
    FakeSMTP.fail_login = FakeSMTP.fail_send = False


@pytest.fixture
def mail_env(monkeypatch):
    for k, v in MAIL_ENV.items():
        monkeypatch.setenv(k, v)
    for k in ("SCOUT_SMTP_HOST", "SCOUT_SMTP_PORT", "SCOUT_MAIL_FROM"):
        monkeypatch.delenv(k, raising=False)


# --------------------------------------------------------------------------- settings, sending


def test_settings_defaults_and_password_spaces(mail_env):
    s = MailSettings.from_env()
    assert (s.host, s.port) == ("smtp.gmail.com", 587)
    assert s.password == "abcdefghijklmnop"
    assert s.sender == s.user == "russell.scout@gmail.com"
    assert s.to == "russell@example.com"


def test_settings_missing_says_how_to_fix(monkeypatch):
    for k in MAIL_ENV:
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(digest.DigestError, match="make-mail-env.sh"):
        MailSettings.from_env()


def test_send_uses_starttls_then_login(mail_env):
    s = MailSettings.from_env()
    msg = digest.build_message(s, "subject", "body\n", "<p>body</p>")
    digest.send(s, msg, smtp_class=FakeSMTP)
    kinds = [c[0] for c in FakeSMTP.calls]
    assert kinds.index("starttls") < kinds.index("login") < kinds.index("send")
    assert ("login", "russell.scout@gmail.com", "abcdefghijklmnop") in FakeSMTP.calls
    assert ("send", "russell.scout@gmail.com", ("russell@example.com",)) in FakeSMTP.calls
    sent = FakeSMTP.sent[0]
    assert sent["From"] == "Scout <russell.scout@gmail.com>"
    assert sent.get_body(("plain",)).get_content() == "body\n"
    assert "<p>body</p>" in sent.get_body(("html",)).get_content()


def test_rejected_login_is_explained(mail_env):
    FakeSMTP.fail_login = True
    s = MailSettings.from_env()
    with pytest.raises(digest.DigestError, match="app password"):
        digest.send(s, digest.build_message(s, "x", "y", None), smtp_class=FakeSMTP)


def test_test_email_needs_no_database(mail_env, monkeypatch):
    monkeypatch.setenv("SCOUT_READER_PASSWORD", "")  # a database connection would fail
    lines: list[str] = []
    assert digest.run(test_email=True, out=lines.append, smtp_class=FakeSMTP) == 0
    assert FakeSMTP.sent[0]["Subject"] == "Scout: test email"
    assert "Sent a test email to russell@example.com" in lines[0]


def test_test_email_without_settings_fails_clearly(monkeypatch):
    for k in MAIL_ENV:
        monkeypatch.delenv(k, raising=False)
    lines: list[str] = []
    assert digest.run(test_email=True, out=lines.append, smtp_class=FakeSMTP) == 1
    assert "SCOUT_SMTP_USER" in lines[0] and not FakeSMTP.sent


# --------------------------------------------------------------------------- rendering


def sample_digest(problems=None) -> Digest:
    now = datetime(2026, 11, 1, 14, 10, tzinfo=UTC)
    new = [
        Job("AI Engineer", "Remarcable, Inc.", "Vancouver (hybrid)",
            "https://jobs.ashbyhq.com/remarcable-inc/x", pay="CAD 120,000–150,000"),
        Job("Data <Scientist> & co", "A&B", "Burnaby", None),
    ]  # fmt: skip
    return Digest(
        covers_from=now - timedelta(days=1), covers_to=now, day="Sun Nov 1",
        since="Sat Oct 31 07:10", first=False, relevant_open=150,
        sections=[
            Section(NEW, "strong", new, 30),
            Section(POSSIBLE, "loose", [], 0),
            Section(CLOSED, "gone", [], 0),
            Section(SUSPICIOUS, "flags", [], 0),
        ],
        health=["sweeps: 2 of 2 ok"], problems=problems or [],
    )  # fmt: skip


def test_subject_counts():
    assert sample_digest().subject == "Scout: 30 new jobs, 0 suspicious (Sun Nov 1)"
    d = sample_digest(problems=["x"])
    assert d.subject == "Scout: 30 new jobs, 0 suspicious, 1 problem (Sun Nov 1)"
    d.sections[0].total = 0
    assert d.subject.startswith("Scout: no new jobs,")


def test_text_lists_jobs_links_and_more():
    t = sample_digest(problems=["The last backup failed: x"]).text()
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
def test_sent_digest_is_recorded_and_the_next_starts_after_it(data, mail_env, tmp_path):
    lines: list[str] = []
    assert digest.run(out=lines.append, logs_dir=tmp_path, smtp_class=FakeSMTP) == 0, lines
    assert FakeSMTP.sent[0]["Subject"].startswith("Scout: 2 new jobs, 1 suspicious, 1 problem (")
    assert FakeSMTP.sent[0]["To"] == "russell@example.com"
    with db.connect("reader") as c:
        row = c.execute(
            "SELECT new_relevant, new_possible, closed, suspicious, problems FROM digests"
        ).fetchall()
    assert row == [(2, 1, 1, 1, 1)]

    with db.connect("ingest", autocommit=True) as c:
        add_posting(c, "Machine Learning Engineer", 0)
    assert digest.run(out=lines.append, logs_dir=tmp_path, smtp_class=FakeSMTP) == 0
    second = FakeSMTP.sent[1]
    assert second["Subject"].startswith("Scout: 1 new job, 0 suspicious,")
    assert "Machine Learning Engineer" in second.get_body(("plain",)).get_content()
    assert "AI Engineer —" not in second.get_body(("plain",)).get_content()


@needs_db
def test_failed_send_records_nothing(data, mail_env, tmp_path):
    FakeSMTP.fail_send = True
    lines: list[str] = []
    assert digest.run(out=lines.append, logs_dir=tmp_path, smtp_class=FakeSMTP) == 1
    assert "Nothing recorded" in lines[-1]
    with db.connect("reader") as c:
        assert c.execute("SELECT count(*) FROM digests").fetchone()[0] == 0


@needs_db
def test_dry_run_prints_and_records_nothing(data, monkeypatch, tmp_path):
    for k in MAIL_ENV:
        monkeypatch.delenv(k, raising=False)  # not needed for a dry run
    lines: list[str] = []
    assert digest.run(dry_run=True, out=lines.append, logs_dir=tmp_path) == 0
    assert lines[0].startswith("Subject: Scout: 2 new jobs")
    assert lines[-1] == "(dry run: nothing sent, nothing recorded)"
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
    assert d.subject.startswith("Scout: no new jobs, 0 suspicious, 2 problems")
