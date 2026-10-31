"""`scout sweep`: run the WorkBC search for every configured term.

Today only `--dry-run` exists: it fetches, classifies every posting (area, workplace,
title match, job-board system, employer) and prints a summary. Nothing is written to the
database yet; saving comes with task 1.6.

  scout sweep --dry-run                       live search, raw responses saved
  scout sweep --dry-run --replay sweep-…Z     re-run the rules on a saved sweep, offline

Raw responses go to $SCOUT_RAW_DIR/sweep-<time>/ when that variable is set (the scout
container sets it to /data/workbc-raw, which is $SCOUT_RAW_DIR_HOST =
/srv/scout/logs/workbc on the box; the summary prints the box path).
"""

from __future__ import annotations

import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from scout import config
from scout.pipeline.record import PostingRecord, build
from scout.sources import workbc

PUBLIC_API_ATS = ("ashby", "greenhouse", "lever")


def raw_dir_for_run(now: datetime | None = None) -> Path | None:
    base = os.environ.get("SCOUT_RAW_DIR")
    if not base:
        return None
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%SZ")
    return Path(base) / f"sweep-{stamp}"


def shown_path(path: Path) -> str:
    """The path as it is on the box, not inside the container."""
    base, host = os.environ.get("SCOUT_RAW_DIR"), os.environ.get("SCOUT_RAW_DIR_HOST")
    if base and host:
        try:
            return str(Path(host) / path.relative_to(base))
        except ValueError:
            pass
    return str(path)


def replay_post(folder: Path) -> workbc.Post:
    """A stand-in for the HTTP call that serves pages saved by an earlier sweep."""

    def post(url: str, body: dict) -> bytes:
        name = f"{workbc.slug(body['Keyword'])}-p{body['Page']}.json"
        try:
            return (folder / name).read_bytes()
        except FileNotFoundError:
            raise workbc.HttpError(404, f"replay: {name} not in this sweep".encode()) from None

    return post


def _counts(counter: Counter, n: int = 15) -> str:
    return ", ".join(f"{k} {v}" for k, v in counter.most_common(n)) or "-"


def summarize(result: workbc.SweepResult, records: list[PostingRecord], out=print) -> None:
    out(f"{'term':26} {'outcome':10} {'count':>5} {'got':>5} {'pages':>5}  note")
    for t in result.terms:
        count = "-" if t.count is None else str(t.count)
        note = t.error or ("" if t.complete else "incomplete")
        out(f"{t.term:26} {t.outcome:10} {count:>5} {len(t.jobs):>5} {t.pages:>5}  {note}")

    hits = sum(len(v) for v in result.matched_terms.values())
    multi = sum(1 for v in result.matched_terms.values() if len(v) > 1)
    out("")
    out(
        f"unique postings: {len(records)}  (term hits: {hits}; found by more than one term: {multi})"
    )

    out("")
    out("== where (area)")
    out("  " + _counts(Counter(r.area for r in records)))
    out("  workplace: " + _counts(Counter(r.workplace for r in records)))
    remote = [r for r in records if r.area == "remote"]
    for r in remote[:10]:
        out(f"  remote: {r.title} | {r.employer_name} | {r.city} | {', '.join(r.remote_signs)}")
    elsewhere = Counter(r.city or "(none)" for r in records if r.area not in ("metro", "remote"))
    if elsewhere:
        out("  not Metro Vancouver or remote: " + _counts(elsewhere))

    near = [r for r in records if r.area in ("metro", "remote")]
    out("")
    out(f"== title match (Metro Vancouver or remote: {len(near)})")
    out("  " + _counts(Counter(r.match_quality for r in near)))
    out(f"  relevant (strong match, right place): {sum(r.relevant for r in records)}")
    loose = [r for r in near if r.match_quality == "loose"]
    for r in loose[:12]:
        out(f"  possible: {r.title} | {r.employer_name}")
    if len(loose) > 12:
        out(f"  … and {len(loose) - 12} more possible matches")

    out("")
    out("== employers and job-board systems")
    employers = {r.employer_norm for r in records if r.employer_norm}
    out(f"  employers: {len(employers)} (after merging name variants)")
    out(f"  Job Bank postings (no employer site): {sum(r.is_job_bank for r in records)}")
    out("  systems: " + _counts(Counter(r.ats for r in records)))
    for ats in PUBLIC_API_ATS:
        boards = sorted({r.ats_board for r in records if r.ats == ats and r.ats_board})
        unknown = sum(1 for r in records if r.ats == ats and not r.ats_board)
        line = f"  {ats} boards ({len(boards)}): {', '.join(boards) or '-'}"
        out(line + (f"; {unknown} posting(s) with board not in the link" if unknown else ""))
    custom = Counter(r.link_host for r in records if r.ats == "custom")
    out("  own career sites: " + _counts(custom, 12))

    seconds = result.finished_at - result.started_at
    requests = sum(max(t.pages, 1) for t in result.terms)
    out("")
    out(f"about {requests} requests in {seconds:.0f} s")
    for w in result.warnings:
        out(f"warning: {w}")
    if result.raw_dir:
        out(f"raw responses: {shown_path(result.raw_dir)}")
    if result.suspicious_empty:
        out("SUSPICIOUS: every term returned 0 postings (API change or outage?)")
    out(f"complete sweep: {'yes' if result.complete else 'NO'}")


def run(
    dry_run: bool,
    terms: list[str] | None = None,
    save_raw: bool = True,
    out=print,
    post: workbc.Post = workbc.http_post,
    sleep=time.sleep,
    replay: str | None = None,
) -> int:
    if not dry_run:
        out("Saving to the database comes with task 1.6. For now use: scout sweep --dry-run")
        return 2
    try:
        cfg = config.load()
    except config.ConfigError as e:
        out(f"ERROR: {e}")
        return 1

    chosen = list(cfg.terms)
    if terms:
        unknown = [t for t in terms if t not in cfg.terms]
        if unknown:
            out(f"ERROR: not in {cfg.path.name}: {', '.join(unknown)}")
            return 1
        chosen = terms

    raw_dir = raw_dir_for_run() if save_raw else None
    delay = cfg.sweep.delay_seconds
    if replay:
        folder = Path(replay)
        if not folder.is_absolute() and os.environ.get("SCOUT_RAW_DIR"):
            folder = Path(os.environ["SCOUT_RAW_DIR"]) / folder
        if not folder.is_dir():
            out(f"ERROR: no saved sweep at {shown_path(folder)}")
            return 1
        post, sleep, raw_dir, delay = replay_post(folder), (lambda s: None), None, 0.5
        out(f"WorkBC sweep (dry run, replaying {shown_path(folder)}): {len(chosen)} terms")
    else:
        out(f"WorkBC sweep (dry run): {len(chosen)} terms, titles only, all of B.C.")

    result = workbc.sweep(
        chosen,
        page_size=cfg.sweep.page_size,
        max_pages=cfg.sweep.max_pages_per_term,
        delay=delay,
        api=os.environ.get("SCOUT_WORKBC_API", workbc.DEFAULT_API),
        raw_dir=raw_dir,
        post=post,
        sleep=sleep,
        clock=time.time,
    )
    records = [build(job, result.matched_terms[jid], cfg) for jid, job in result.jobs.items()]
    out("")
    summarize(result, records, out)
    return 0 if result.complete else 1
