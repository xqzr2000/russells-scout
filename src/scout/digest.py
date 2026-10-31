"""`scout digest`: the daily email (Phase 2, tasks 2.3–2.4).

  scout digest                 build the digest, send it, record it in `digests`
  scout digest --dry-run       print it; send nothing, record nothing
  scout digest --test-email    send a short test email (checks mail.env; no database)

The digest covers everything since the last digest that was sent (the first one: the last
24 hours): new relevant jobs, new possible matches, jobs that closed, suspicious postings,
and a health section (sweeps, sources, backup, sync to WSL2, disk). Problems are listed at
the top and counted in the subject.

Reads the database as scout_reader; records the sent digest as scout_ingest. Email settings
come only from the environment (the digest service's mail.env), never from config or the
database, so the recipient can't be changed from inside Scout:

  SCOUT_SMTP_USER       the Gmail address that sends (required)
  SCOUT_SMTP_PASSWORD   its app password (required; spaces are ignored)
  SCOUT_MAIL_TO         who gets the digest (required)
  SCOUT_MAIL_FROM       default: SCOUT_SMTP_USER
  SCOUT_SMTP_HOST       default: smtp.gmail.com
  SCOUT_SMTP_PORT       default: 587 (STARTTLS); 465 means TLS from the start

Box files it reads (mounted read-only under $SCOUT_LOGS_DIR, default /data/logs):
  backup-status   written by ops/backup.sh
  sync/last-sync  written each time WSL2 pulls the backups (task 2.6)
"""

from __future__ import annotations

import html
import os
import shutil
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from scout import db
from scout.status import _money

LIST_LIMIT = 25
FIRST_WINDOW = "24 hours"
WORKBC_JOB_URL = "https://www.workbc.ca/find-job/search-jobs#/job-details/{}"
NO_SWEEP_HOURS = 24
BACKUP_MAX_HOURS = 36
SYNC_MAX_HOURS = 48
DISK_MIN_GB = 5.0
SETUP_HINT = "create /srv/scout/secrets/mail.env with ops/mail/make-mail-env.sh (box-setup §17)"


class DigestError(Exception):
    """A problem worth a one-line message."""


# --------------------------------------------------------------------------- content


@dataclass
class Job:
    title: str
    employer: str
    where: str
    link: str | None
    pay: str = ""
    note: str = ""

    def line(self) -> str:
        parts = [self.title, self.employer or "?", self.where]
        if self.pay:
            parts.append(self.pay)
        text = " — ".join(p for p in parts if p)
        return text + (f"  [{self.note}]" if self.note else "")


@dataclass
class Section:
    title: str
    hint: str
    jobs: list[Job]
    total: int


@dataclass
class Digest:
    covers_from: datetime
    covers_to: datetime
    day: str  # e.g. "Sun Nov 1", in Vancouver time
    since: str  # e.g. "Sat Oct 31 07:10"
    first: bool
    relevant_open: int
    sections: list[Section]
    health: list[str]
    problems: list[str] = field(default_factory=list)

    def count(self, title: str) -> int:
        return next((s.total for s in self.sections if s.title == title), 0)

    @property
    def subject(self) -> str:
        new, sus = self.count(NEW), self.count(SUSPICIOUS)
        jobs = "no new jobs" if new == 0 else f"{new} new job{'s' if new != 1 else ''}"
        parts = [jobs, f"{sus} suspicious"]
        if self.problems:
            parts.append(f"{len(self.problems)} problem{'s' if len(self.problems) != 1 else ''}")
        return f"Scout: {', '.join(parts)} ({self.day})"

    def text(self) -> str:
        lines: list[str] = []
        since = "the last 24 hours (first digest)" if self.first else f"since {self.since}"
        lines.append(f"Scout digest for {self.day}: {since}.")
        lines.append(f"Relevant jobs open right now: {self.relevant_open}.")
        if self.problems:
            lines += ["", "NEEDS YOUR ATTENTION"]
            lines += [f"  ! {p}" for p in self.problems]
        for s in self.sections:
            lines += ["", f"{s.title.upper()} ({s.total})    {s.hint}"]
            if not s.jobs:
                lines.append("  none")
            for j in s.jobs:
                lines.append(f"  {j.line()}")
                if j.link:
                    lines.append(f"    {j.link}")
            if s.total > len(s.jobs):
                lines.append(f"  … and {s.total - len(s.jobs)} more (`scout status`)")
        lines += ["", "HEALTH"] + [f"  {h}" for h in self.health]
        lines += ["", "Sent automatically by Scout, from the box."]
        return "\n".join(lines) + "\n"

    def html(self) -> str:
        e = html.escape
        since = "the last 24 hours (first digest)" if self.first else f"since {e(self.since)}"
        out = [
            (
                '<!DOCTYPE html><html><body style="font-family:-apple-system,Segoe UI,Arial,'
                'sans-serif;font-size:14px;line-height:1.45;color:#222;max-width:720px">'
            ),
            (
                f"<p>Scout digest for <b>{e(self.day)}</b>: {since}.<br>"
                f"Relevant jobs open right now: <b>{self.relevant_open}</b>.</p>"
            ),
        ]
        if self.problems:
            out.append('<h3 style="color:#b00020;margin-bottom:4px">Needs your attention</h3><ul>')
            out += [f"<li>{e(p)}</li>" for p in self.problems]
            out.append("</ul>")
        for s in self.sections:
            out.append(
                f'<h3 style="margin-bottom:2px">{e(s.title)} ({s.total})</h3>'
                f'<div style="color:#666;font-size:12px">{e(s.hint)}</div>'
            )
            if not s.jobs:
                out.append("<p>none</p>")
                continue
            out.append('<ul style="padding-left:20px">')
            for j in s.jobs:
                title = e(j.title)
                if j.link:
                    title = f'<a href="{e(j.link, quote=True)}">{title}</a>'
                rest = " — ".join(e(x) for x in (j.employer or "?", j.where, j.pay) if x)
                note = f' <span style="color:#b00020">[{e(j.note)}]</span>' if j.note else ""
                out.append(f"<li>{title} — {rest}{note}</li>")
            if s.total > len(s.jobs):
                out.append(
                    f"<li>… and {s.total - len(s.jobs)} more (<code>scout status</code>)</li>"
                )
            out.append("</ul>")
        out.append('<h3 style="margin-bottom:4px">Health</h3><ul>')
        out += [f"<li>{e(h)}</li>" for h in self.health]
        out.append('</ul><p style="color:#888;font-size:12px">Sent automatically by Scout.</p>')
        out.append("</body></html>")
        return "\n".join(out)


NEW = "New relevant jobs"
POSSIBLE = "New possible matches"
CLOSED = "Closed"
SUSPICIOUS = "Suspicious"


# --------------------------------------------------------------------------- gathering

_JOB_COLUMNS = (
    "p.title, COALESCE(e.name, p.employer_name), COALESCE(p.city, ''),"
    " COALESCE(NULLIF(p.ats_workplace, 'unknown'), p.workplace),"
    " p.pay_min, p.pay_max, p.pay_currency, p.pay_period, p.jump_url, p.workbc_job_id,"
    " p.trust_score, p.trust_reasons"
)
_FROM = " FROM postings p LEFT JOIN employers e ON e.id = p.employer_id"
_IN_AREA = " p.area IN ('metro', 'remote')"


def _job(row, note: str = "") -> Job:
    title, employer, city, workplace, lo, hi, cur, period, jump, jid, *_ = row
    pay = ""
    if lo is not None or hi is not None:
        pay = f"{cur or ''} {_money(lo)}–{_money(hi)}".strip()
        pay += f"/{period}" if period and period != "year" else ""
    where = city if workplace in (None, "unknown") else f"{city} ({workplace})".strip()
    link = jump or (WORKBC_JOB_URL.format(jid) if jid else None)
    return Job(title=title, employer=employer, where=where, link=link, pay=pay, note=note)


def _hours_ago(when: datetime, now: datetime) -> float:
    return (now - when).total_seconds() / 3600


def _ago(hours: float) -> str:
    if hours < 1:
        return "less than an hour ago"
    if hours < 48:
        return f"{hours:.0f} hour{'s' if round(hours) != 1 else ''} ago"
    return f"{hours / 24:.0f} days ago"


def read_keyvalue(path: Path) -> dict[str, str] | None:
    try:
        text = path.read_text()
    except OSError:
        return None
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def _backup_health(logs: Path, now: datetime, health: list[str], problems: list[str]) -> None:
    s = read_keyvalue(logs / "backup-status")
    if s is None:
        health.append("backup: no backup recorded yet")
        problems.append("No database backup has been recorded (ops/backup.sh, box-setup §16).")
        return
    try:
        when = datetime.fromisoformat(s.get("time", ""))
    except ValueError:
        health.append("backup: status file unreadable")
        problems.append("The backup status file can't be read.")
        return
    hours = _hours_ago(when, now)
    size = int(s.get("bytes", "0") or 0)
    if s.get("result") != "ok":
        health.append(f"backup: FAILED {_ago(hours)}: {s.get('message', '')}")
        problems.append(f"The last backup failed: {s.get('message', '')}")
        return
    health.append(f"backup: {_ago(hours)}, {size / 1e6:.1f} MB, {s.get('message', 'ok')}")
    if hours > BACKUP_MAX_HOURS:
        problems.append(f"The last good backup is {_ago(hours)}.")


def _sync_health(logs: Path, now: datetime, health: list[str], problems: list[str]) -> None:
    stamp = logs / "sync" / "last-sync"
    try:
        text = stamp.read_text().strip()
    except OSError:
        health.append("sync to WSL2: not set up yet")
        return
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        when = datetime.fromtimestamp(stamp.stat().st_mtime, UTC)
    hours = _hours_ago(when, now)
    health.append(f"sync to WSL2: {_ago(hours)}")
    if hours > SYNC_MAX_HOURS:
        problems.append(f"WSL2 hasn't pulled the backups for {_ago(hours).removesuffix(' ago')}.")


def _disk_health(logs: Path, health: list[str], problems: list[str]) -> None:
    try:
        free = shutil.disk_usage(logs).free / 1e9
    except OSError:
        return
    health.append(f"disk: {free:.0f} GB free")
    if free < DISK_MIN_GB:
        problems.append(f"Only {free:.1f} GB of disk left on the box.")


def gather(conn, logs_dir: Path | None = None, limit: int = LIST_LIMIT) -> Digest:
    tz = os.environ.get("SCOUT_TZ", "America/Vancouver")
    logs = logs_dir or Path(os.environ.get("SCOUT_LOGS_DIR", "/data/logs"))

    def one(sql, *params):
        return conn.execute(sql, params).fetchone()

    def rows(sql, *params):
        return conn.execute(sql, params).fetchall()

    to, last = one("SELECT now(), (SELECT max(covers_to) FROM digests)")
    first = last is None
    frm = one(f"SELECT now() - interval '{FIRST_WINDOW}'")[0] if first else last
    day, since = one(
        "SELECT to_char(%s::timestamptz AT TIME ZONE %s, 'Dy Mon FMDD'),"
        " to_char(%s::timestamptz AT TIME ZONE %s, 'Dy Mon FMDD HH24:MI')",
        to, tz, frm, tz,
    )  # fmt: skip
    window = " AND p.first_seen_at > %s AND p.first_seen_at <= %s"

    def section(title, hint, where, params, order="p.first_seen_at DESC, p.title", note=None):
        total = one(f"SELECT count(*){_FROM} WHERE {where}", *params)[0]
        found = rows(
            f"SELECT {_JOB_COLUMNS}{_FROM} WHERE {where} ORDER BY {order} LIMIT %s",
            *params, limit,
        )  # fmt: skip
        jobs = [_job(r, note(r) if note else "") for r in found]
        return Section(title, hint, jobs, total)

    active_in_area = f"p.status = 'active' AND {_IN_AREA}"
    trusted = " AND COALESCE(p.trust_score, 0) >= 0"  # suspicious ones get their own section
    sections = [
        section(
            NEW, "Metro Vancouver or remote, strong title match",
            f"{active_in_area} AND p.match_quality = 'exact'{trusted}{window}", (frm, to),
        ),
        section(
            POSSIBLE, "looser title match: worth a glance",
            f"{active_in_area} AND p.match_quality = 'loose'{trusted}{window}", (frm, to),
        ),
        section(
            CLOSED, "gone from WorkBC or the job board (missed 3 sweeps over 24 hours)",
            f"{_IN_AREA} AND p.status = 'removed' AND EXISTS (SELECT 1 FROM posting_events ev"
            " WHERE ev.posting_id = p.id AND ev.kind = 'removed' AND ev.at > %s AND ev.at <= %s)",
            (frm, to), order="p.title",
        ),
        section(
            SUSPICIOUS, "red flags outweigh good signs; kept, not hidden (v_suspicious)",
            f"p.status = 'active' AND p.trust_score < 0{window}", (frm, to),
            note=lambda r: f"{r[10]:+d}: " + ", ".join(r[11] or []),
        ),
    ]  # fmt: skip
    relevant_open = one(
        f"SELECT count(*){_FROM} WHERE {active_in_area} AND p.match_quality = 'exact'{trusted}"
    )[0]

    health: list[str] = []
    problems: list[str] = []
    total, ok = one(
        "SELECT count(*), count(*) FILTER (WHERE ok) FROM scout_runs"
        " WHERE kind = 'sweep' AND started_at > %s AND started_at <= %s",
        frm, to,
    )  # fmt: skip
    last_ok, last_ok_text = one(
        "SELECT max(finished_at), to_char(max(finished_at) AT TIME ZONE %s, 'Dy HH24:MI')"
        " FROM scout_runs WHERE kind = 'sweep' AND ok",
        tz,
    )
    period = "in the last 24 hours" if first else "since the last digest"
    health.append(
        f"sweeps: {ok} of {total} ok {period}"
        + (f"; last good sweep {last_ok_text}" if last_ok else "; no good sweep yet")
    )
    if last_ok is None or _hours_ago(last_ok, to) > NO_SWEEP_HOURS:
        problems.append(
            "No successful sweep in the last 24 hours. Check `systemctl list-timers 'scout-*'`"
            " and `journalctl -u scout-sweep -n 50`."
        )
    elif total > ok:
        problems.append(f"{total - ok} sweep(s) since the last digest didn't finish cleanly.")

    trouble = rows(
        "SELECT name, status, consecutive_failures FROM v_source_health"
        " WHERE status <> 'active' OR consecutive_failures > 0 ORDER BY name"
    )
    if trouble:
        names = ", ".join(f"{n} ({s}, {f} failure(s))" for n, s, f in trouble)
        health.append(f"sources needing attention: {names}")
        if any(s != "active" for _n, s, _f in trouble):
            problems.append("Some sources are degraded or blocked; see `scout status`.")
    else:
        health.append(
            "sources: all ok ("
            + str(one("SELECT count(*) FROM sources WHERE kind = 'ats'")[0])
            + " job boards and WorkBC)"
        )
    sus_open = one("SELECT count(*) FROM v_suspicious WHERE status = 'active'")[0]
    if sus_open:
        health.append(f"suspicious postings open in total: {sus_open} (v_suspicious)")

    _backup_health(logs, to, health, problems)
    _sync_health(logs, to, health, problems)
    _disk_health(logs, health, problems)

    return Digest(
        covers_from=frm, covers_to=to, day=day, since=since, first=first,
        relevant_open=relevant_open, sections=sections, health=health, problems=problems,
    )  # fmt: skip


# --------------------------------------------------------------------------- email


@dataclass(frozen=True)
class MailSettings:
    host: str
    port: int
    user: str
    password: str
    sender: str
    to: str

    @classmethod
    def from_env(cls) -> MailSettings:
        env = os.environ
        missing = [
            v for v in ("SCOUT_SMTP_USER", "SCOUT_SMTP_PASSWORD", "SCOUT_MAIL_TO") if not env.get(v)
        ]
        if missing:
            raise DigestError(f"{', '.join(missing)} not set: {SETUP_HINT}")
        try:
            port = int(env.get("SCOUT_SMTP_PORT", "587"))
        except ValueError as e:
            raise DigestError("SCOUT_SMTP_PORT must be a number (587)") from e
        user = env["SCOUT_SMTP_USER"].strip()
        return cls(
            host=env.get("SCOUT_SMTP_HOST", "smtp.gmail.com").strip(),
            port=port,
            user=user,
            password="".join(env["SCOUT_SMTP_PASSWORD"].split()),
            sender=env.get("SCOUT_MAIL_FROM", "").strip() or user,
            to=env["SCOUT_MAIL_TO"].strip(),
        )


def build_message(
    settings: MailSettings, subject: str, text: str, html_body: str | None
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = f"Scout <{settings.sender}>"
    msg["To"] = settings.to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain=settings.sender.rpartition("@")[2] or "scout.local")
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    return msg


def send(settings: MailSettings, msg: EmailMessage, smtp_class=None) -> None:
    context = ssl.create_default_context()
    try:
        if settings.port == 465:
            cls = smtp_class or smtplib.SMTP_SSL
            server = cls(settings.host, settings.port, timeout=30, context=context)
        else:
            cls = smtp_class or smtplib.SMTP
            server = cls(settings.host, settings.port, timeout=30)
        with server:
            if settings.port != 465:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
            server.login(settings.user, settings.password)
            server.send_message(msg, from_addr=settings.user, to_addrs=[settings.to])
    except smtplib.SMTPAuthenticationError as e:
        raise DigestError(
            f"{settings.host} refused the login for {settings.user} ({e.smtp_code}). Check the"
            " address and the app password in mail.env; a revoked app password needs a new one."
        ) from e
    except (smtplib.SMTPException, OSError) as e:
        raise DigestError(f"sending through {settings.host}:{settings.port} failed: {e}") from e


# --------------------------------------------------------------------------- command


def run(
    dry_run: bool = False,
    test_email: bool = False,
    out=print,
    logs_dir: Path | None = None,
    smtp_class=None,
) -> int:
    settings = None
    if not dry_run:
        try:
            settings = MailSettings.from_env()
        except DigestError as e:
            out(f"ERROR: {e}")
            return 1

    if test_email:
        assert settings is not None
        when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        msg = build_message(
            settings,
            "Scout: test email",
            f"If you can read this, Scout's daily digest can reach you.\n\nSent {when} from the"
            f" box, through {settings.host} as {settings.user}.\n",
            None,
        )
        try:
            send(settings, msg, smtp_class)
        except DigestError as e:
            out(f"ERROR: {e}")
            return 1
        out(f"Sent a test email to {settings.to} through {settings.host} as {settings.user}.")
        out("Not there in a minute or two? Check the spam folder.")
        return 0

    try:
        reader = db.connect("reader")
    except db.DbError as e:
        out(f"ERROR: {e}")
        return 1
    with reader:
        d = gather(reader, logs_dir=logs_dir)

    if dry_run:
        out(f"Subject: {d.subject}")
        out("")
        out(d.text().rstrip("\n"))
        out("")
        out("(dry run: nothing sent, nothing recorded)")
        return 0

    assert settings is not None
    msg = build_message(settings, d.subject, d.text(), d.html())
    try:
        send(settings, msg, smtp_class)
    except DigestError as e:
        out(f"ERROR: {e}")
        out("Nothing recorded: the next digest will cover this period too.")
        return 1
    try:
        with db.connect("ingest") as conn:
            conn.execute(
                "INSERT INTO digests (covers_from, covers_to, subject, new_relevant, new_possible,"
                " closed, suspicious, problems) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (d.covers_from, d.covers_to, d.subject, d.count(NEW), d.count(POSSIBLE),
                 d.count(CLOSED), d.count(SUSPICIOUS), len(d.problems)),
            )  # fmt: skip
    except db.DbError as e:
        out(f"Sent, but couldn't record it ({e}); the next digest will repeat this period.")
        return 1
    out(f"Sent: {d.subject} (to {settings.to})")
    return 0
