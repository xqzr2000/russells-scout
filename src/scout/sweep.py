"""`scout sweep`: run the WorkBC search for every configured term.

Today (tasks 1.2–1.3) only `--dry-run` exists: it fetches, prints a summary and saves the
raw responses, and writes nothing to the database. Classification (1.4), the link
classifier (1.5) and saving to Postgres (1.6) plug in here next.

Raw responses go to $SCOUT_RAW_DIR/sweep-<time>/ when that variable is set (the scout
container sets it to /data/workbc-raw, which is /srv/scout/logs/workbc on the box).
"""

from __future__ import annotations

import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from scout import config
from scout.sources import workbc


def raw_dir_for_run(now: datetime | None = None) -> Path | None:
    base = os.environ.get("SCOUT_RAW_DIR")
    if not base:
        return None
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%d-%H%M%SZ")
    return Path(base) / f"sweep-{stamp}"


def summarize(result: workbc.SweepResult, out=print) -> None:
    out(f"{'term':26} {'outcome':10} {'count':>5} {'got':>5} {'pages':>5}  note")
    for t in result.terms:
        count = "-" if t.count is None else str(t.count)
        note = t.error or ("" if t.complete else "incomplete")
        out(f"{t.term:26} {t.outcome:10} {count:>5} {len(t.jobs):>5} {t.pages:>5}  {note}")

    jobs = list(result.jobs.values())
    hits = sum(len(v) for v in result.matched_terms.values())
    out("")
    out(f"unique postings: {len(jobs)}  (term hits: {hits})")
    out(f"Job Bank postings: {sum(1 for j in jobs if j.get('IsFederalJob'))}")
    multi = sum(1 for v in result.matched_terms.values() if len(v) > 1)
    out(f"found by more than one term: {multi}")

    cities = Counter(j.get("City") or "(none)" for j in jobs)
    out("cities: " + ", ".join(f"{c} {n}" for c, n in cities.most_common(15)))

    def host(job: dict) -> str:
        url = workbc.jump_url(job)
        return urlparse(url).netloc.lower() if url else "(no link)"

    hosts = Counter(host(j) for j in jobs)
    out("link hosts: " + ", ".join(f"{h} {n}" for h, n in hosts.most_common(15)))

    seconds = result.finished_at - result.started_at
    requests = sum(max(t.pages, 1) for t in result.terms)
    out("")
    out(f"about {requests} requests in {seconds:.0f} s")
    for w in result.warnings:
        out(f"warning: {w}")
    if result.raw_dir:
        out(f"raw responses: {result.raw_dir}")
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
    out(f"WorkBC sweep (dry run): {len(chosen)} terms, titles only, all of B.C.")
    result = workbc.sweep(
        chosen,
        page_size=cfg.sweep.page_size,
        max_pages=cfg.sweep.max_pages_per_term,
        delay=cfg.sweep.delay_seconds,
        api=os.environ.get("SCOUT_WORKBC_API", workbc.DEFAULT_API),
        raw_dir=raw_dir,
        post=post,
        sleep=sleep,
        clock=time.time,
    )
    out("")
    summarize(result, out)
    return 0 if result.complete else 1
