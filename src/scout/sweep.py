"""`scout sweep`: run the WorkBC search for every configured term.

Fetches every term, classifies every posting (area, workplace, title match, job-board
system, employer), prints a summary and saves the sweep to Postgres as scout_ingest
(src/scout/store.py), then reads the Ashby, Greenhouse and Lever boards
(src/scout/enrich.py) unless --no-enrich.

  scout sweep                                 live search, saved to the database
  scout sweep --dry-run                       live search, nothing written to the database
  scout sweep --dry-run --replay sweep-…Z     re-run the rules on a saved sweep, offline

Raw responses are saved in both live modes.

Raw responses go to $SCOUT_RAW_DIR/sweep-<time>/ when that variable is set (the scout
container sets it to /data/workbc-raw, which is $SCOUT_RAW_DIR_HOST =
/srv/scout/logs/workbc on the box; the summary prints the box path). Folders older than
RAW_KEEP_DAYS are deleted after each live sweep.

Only one sweep or enrichment runs at a time (db.try_run_lock); a second one is skipped.
"""

from __future__ import annotations

import os
import shutil
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scout import config, db, enrich, store, trust
from scout.pipeline import dupes
from scout.pipeline.record import PostingRecord, build
from scout.sources import workbc

PUBLIC_API_ATS = ("ashby", "greenhouse", "lever")
RAW_KEEP_DAYS = 30
RAW_STAMP = "%Y%m%d-%H%M%SZ"


def raw_dir_for_run(now: datetime | None = None) -> Path | None:
    base = os.environ.get("SCOUT_RAW_DIR")
    if not base:
        return None
    stamp = (now or datetime.now(UTC)).strftime(RAW_STAMP)
    return Path(base) / f"sweep-{stamp}"


def prune_raw(base: Path, now: datetime | None = None, keep_days: int = RAW_KEEP_DAYS) -> int:
    """Delete sweep-<time> folders older than keep_days; returns how many. Other names stay."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=keep_days)
    removed = 0
    for folder in sorted(base.glob("sweep-*")):
        try:
            when = datetime.strptime(folder.name[len("sweep-") :], RAW_STAMP).replace(tzinfo=UTC)
        except ValueError:
            continue
        if folder.is_dir() and when < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed


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


def summarize(
    result: workbc.SweepResult, records: list[PostingRecord], out=print, replayed: bool = False
) -> None:
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

    now = datetime.now(UTC)
    items = [
        dupes.Item(r.workbc_job_id, r.canonical_url, r.employer_norm, r.title, now, r.jump_url)
        for r in records
    ]
    groups: dict = {}
    for job_id, group in dupes.groups(items).items():
        groups.setdefault(group, []).append(job_id)
    by_id = {r.workbc_job_id: r for r in records}
    out("")
    scored = [(trust.score(trust_input(r)), r) for r in records]
    flagged = [(s, r) for s, r in scored if s[0] < 0]
    out("== trust (before job-board details)")
    out("  scores: " + _counts(Counter(s[0] for s, _ in scored), 10))
    out(f"  suspicious (below 0): {len(flagged)}")
    for (value, reasons), r in flagged[:10]:
        out(f"  {value:+d} {r.title} | {r.employer_name} | {', '.join(reasons)}")

    out("")
    out(f"== likely duplicates: {len(groups)} group(s), {sum(map(len, groups.values()))} postings")
    for members in list(groups.values())[:8]:
        out("  " + " | ".join(f"{by_id[m].employer_name} ({by_id[m].link_host})" for m in members))
        out(f"    {by_id[members[0]].title}")

    seconds = result.finished_at - result.started_at
    pages = sum(max(t.pages, 1) for t in result.terms)
    out("")
    if replayed:
        out(f"{pages} saved pages read")
    else:
        out(f"about {pages} requests in {seconds:.0f} s")
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
    enrich_after: bool = True,
    ats_get=None,
) -> int:
    if replay and not dry_run:
        out("ERROR: --replay only works with --dry-run (a replay must not change the database)")
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

    conn = None
    if not dry_run:
        try:
            conn = db.connect("ingest", autocommit=True)
        except db.DbError as e:
            out(f"ERROR: {e}")
            return 1
        if not db.try_run_lock(conn):
            conn.close()
            out("Another sweep or enrichment is running; this one is skipped.")
            return 0

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
        mode = "dry run" if dry_run else "saving to the database"
        out(f"WorkBC sweep ({mode}): {len(chosen)} terms, titles only, all of B.C.")

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
    summarize(result, records, out, replayed=bool(replay))
    if raw_dir is not None:
        pruned = prune_raw(raw_dir.parent)
        if pruned:
            out(f"raw responses older than {RAW_KEEP_DAYS} days deleted: {pruned} sweep(s)")

    if conn is not None:
        with conn:
            stats = store.save_sweep(
                conn, result, records, cfg, all_terms=list(chosen) == list(cfg.terms)
            )
            report_saved(stats, out)
            if enrich_after:
                kwargs = {"get": ats_get} if ats_get else {}
                estats = enrich.enrich(conn, cfg, sleep=sleep, **kwargs)
                enrich.report(estats, out)
    return 0 if result.complete else 1


def trust_input(r: PostingRecord) -> trust.TrustInput:
    return trust.TrustInput(
        title=r.title, employer_name=r.employer_name, employer_norm=r.employer_norm, ats=r.ats,
        ats_board=r.ats_board, jump_url=r.jump_url, link_host=r.link_host,
        is_job_bank=r.is_job_bank, found_via="workbc", enriched=False,
        salary_text=r.salary_text,
    )  # fmt: skip


def report_saved(stats: store.SaveStats, out=print) -> None:
    out("")
    out(f"== saved to the database (run {stats.run_id})")
    out(
        f"  new {stats.new}, changed {stats.changed}, seen again {stats.seen_again},"
        f" reappeared {stats.reappeared}; new employers {stats.employers_new}"
    )
    if stats.skipped_area:
        out(f"  not saved because of the area settings: {stats.skipped_area}")
    out(
        f"  likely duplicates: {stats.dup_groups} group(s), {stats.dup_postings} postings"
        f" ({stats.dup_changed} changed this run; see v_duplicates)"
    )
    out(f"  suspicious postings (trust score below 0): {stats.suspicious}; see v_suspicious")
    if stats.removal_checked:
        out(
            f"  not seen this time: {stats.marked_missed} (counted as missed);"
            f" removed after {store.MISSES_BEFORE_REMOVED} misses: {stats.removed}"
        )
    for note in stats.notes:
        out(f"  {note}")
