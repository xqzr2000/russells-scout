"""`scout digest`: the daily digest, saved as files on the box (Phase 2, task 2.3).

  scout digest              build the digest, write it to files, record it in `digests`
  scout digest --dry-run    print it; write nothing, record nothing

The digest covers everything since the last digest written (the first one: the last 24
hours): new relevant jobs, new possible matches, jobs that closed, suspicious postings, and
a health section (sweeps, sources, backup, sync to WSL2, disk). Problems are listed at the
top and counted in the title line.

Files, in $SCOUT_DIGESTS_DIR (default /data/digests; /srv/scout/logs/digests on the box):
  <YYYY-MM-DD-HHMM>.txt and .html   one pair per digest, Vancouver time
  latest.txt, latest.html           copies of the newest pair
Pairs older than KEEP_DAYS are deleted. Nothing leaves the box: there is no email.

Reads the database as scout_reader; records the digest as scout_ingest. Box files it reads
(mounted read-only under $SCOUT_LOGS_DIR, default /data/logs):
  backup-status   written by ops/backup.sh
  sync/last-sync  written each time WSL2 pulls the backups (task 2.6)
"""

from __future__ import annotations

import html
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
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
KEEP_DAYS = 90
STAMP_RE = r"^\d{4}-\d{2}-\d{2}-\d{4}$"


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
    stamp: str  # e.g. "2026-11-01-0710": the file name
    since: str  # e.g. "Sat Oct 31 07:10"
    first: bool
    relevant_open: int
    sections: list[Section]
    health: list[str]
    problems: list[str] = field(default_factory=list)

    def count(self, title: str) -> int:
        return next((s.total for s in self.sections if s.title == title), 0)

    @property
    def title(self) -> str:
        new, sus = self.count(NEW), self.count(SUSPICIOUS)
        jobs = "no new jobs" if new == 0 else f"{new} new job{'s' if new != 1 else ''}"
        parts = [jobs, f"{sus} suspicious"]
        if self.problems:
            parts.append(f"{len(self.problems)} problem{'s' if len(self.problems) != 1 else ''}")
        return f"Scout: {', '.join(parts)} ({self.day})"

    def text(self) -> str:
        lines: list[str] = [self.title, ""]
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
        lines += ["", f"Written by Scout on the box ({self.stamp})."]
        return "\n".join(lines) + "\n"

    def html(self) -> str:
        e = html.escape
        since = "the last 24 hours (first digest)" if self.first else f"since {e(self.since)}"
        out = [
            (
                f'<!DOCTYPE html><html><head><meta charset="utf-8"><title>{e(self.title)}</title>'
                '</head><body style="font-family:-apple-system,Segoe UI,Arial,'
                'sans-serif;font-size:14px;line-height:1.45;color:#222;max-width:720px">'
            ),
            f"<h2>{e(self.title)}</h2>",
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
    day, since, stamp = one(
        "SELECT to_char(%s::timestamptz AT TIME ZONE %s, 'Dy Mon FMDD'),"
        " to_char(%s::timestamptz AT TIME ZONE %s, 'Dy Mon FMDD HH24:MI'),"
        " to_char(%s::timestamptz AT TIME ZONE %s, 'YYYY-MM-DD-HH24MI')",
        to, tz, frm, tz, to, tz,
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
        covers_from=frm, covers_to=to, day=day, stamp=stamp, since=since, first=first,
        relevant_open=relevant_open, sections=sections, health=health, problems=problems,
    )  # fmt: skip


# --------------------------------------------------------------------------- files


def shown(path: Path) -> str:
    """The path as it is on the box, not inside the container."""
    base, host = os.environ.get("SCOUT_DIGESTS_DIR"), os.environ.get("SCOUT_DIGESTS_DIR_HOST")
    if base and host:
        try:
            return str(Path(host) / path.relative_to(base))
        except ValueError:
            pass
    return str(path)


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(0o644)
    tmp.replace(path)


def write_files(d: Digest, folder: Path) -> Path:
    """Write <stamp>.txt/.html and latest.txt/.html; returns the .txt path."""
    if not folder.is_dir():
        raise DigestError(f"{shown(folder)} doesn't exist (box-setup §17 creates it)")
    text, page = d.text(), d.html()
    try:
        for name, content in (
            (f"{d.stamp}.txt", text), (f"{d.stamp}.html", page),
            ("latest.txt", text), ("latest.html", page),
        ):  # fmt: skip
            _write(folder / name, content)
    except OSError as e:
        raise DigestError(f"can't write to {shown(folder)}: {e.strerror or e}") from e
    return folder / f"{d.stamp}.txt"


def prune(folder: Path, today: datetime, keep_days: int = KEEP_DAYS) -> int:
    """Delete <stamp>.txt/.html older than keep_days; returns how many files."""
    removed = 0
    for f in folder.iterdir():
        if f.suffix not in (".txt", ".html") or not re.match(STAMP_RE, f.stem):
            continue
        try:
            when = datetime.strptime(f.stem[:10], "%Y-%m-%d").replace(tzinfo=UTC)
        except ValueError:
            continue
        if (today - when).days > keep_days:
            f.unlink(missing_ok=True)
            removed += 1
    return removed


# --------------------------------------------------------------------------- command


def run(
    dry_run: bool = False,
    out=print,
    logs_dir: Path | None = None,
    digests_dir: Path | None = None,
) -> int:
    try:
        reader = db.connect("reader")
    except db.DbError as e:
        out(f"ERROR: {e}")
        return 1
    with reader:
        d = gather(reader, logs_dir=logs_dir)

    if dry_run:
        out(d.text().rstrip("\n"))
        out("")
        out("(dry run: nothing written, nothing recorded)")
        return 0

    folder = digests_dir or Path(os.environ.get("SCOUT_DIGESTS_DIR", "/data/digests"))
    try:
        path = write_files(d, folder)
    except DigestError as e:
        out(f"ERROR: {e}")
        out("Nothing recorded: the next digest will cover this period too.")
        return 1
    pruned = prune(folder, d.covers_to)
    try:
        with db.connect("ingest") as conn:
            conn.execute(
                "INSERT INTO digests (covers_from, covers_to, title, file, new_relevant,"
                " new_possible, closed, suspicious, problems)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (d.covers_from, d.covers_to, d.title, path.name, d.count(NEW), d.count(POSSIBLE),
                 d.count(CLOSED), d.count(SUSPICIOUS), len(d.problems)),
            )  # fmt: skip
    except db.DbError as e:
        out(f"Written, but couldn't record it ({e}); the next digest will repeat this period.")
        return 1
    out(f"{d.title}")
    out(f"Written: {shown(path)} (and latest.txt / latest.html)")
    if pruned:
        out(f"Deleted {pruned} digest file(s) older than {KEEP_DAYS} days.")
    return 0
