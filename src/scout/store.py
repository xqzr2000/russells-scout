"""Saving a WorkBC sweep to Postgres (docs/phase1-plan.md §3 steps 8–11, task 1.6).

Everything for one sweep happens in one transaction, as scout_ingest: if anything fails,
nothing is written.

For each posting in the sweep:
  new          insert it, with a `new` event
  seen again   last_seen_at = now, missed_runs = 0, matched terms and classification
               refreshed (rules may have changed; no event for that)
  changed      title, employer, city or salary text differ: update, `changed` event with
               the old and new values
  reappeared   it had been marked removed: back to active, `reappeared` event

Removal safety (plan §3 step 11, acceptance test 4): only after a *complete* sweep of
*every* configured term is each active WorkBC posting that wasn't seen given
missed_runs + 1; at 3 it becomes `removed` with a `removed` event. An incomplete sweep, or a
`--term` subset, marks nothing.

Areas kept: metro and unknown always; remote if area.include_remote; other_bc and outside
if area.keep_other_bc.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import psycopg
from psycopg.types.json import Jsonb

from scout import trust
from scout.config import SearchConfig
from scout.pipeline import dupes
from scout.pipeline.record import PostingRecord
from scout.sources.workbc import SweepResult

WORKBC_SOURCE = "workbc"
WORKBC_URL = "https://www.workbc.ca/find-job/search-jobs"
MISSES_BEFORE_REMOVED = 3
WATCHED_FIELDS = ("title", "employer_name", "city", "salary_text")


@dataclass
class SaveStats:
    run_id: int | None = None
    new: int = 0
    changed: int = 0
    seen_again: int = 0
    reappeared: int = 0
    skipped_area: int = 0
    marked_missed: int = 0
    removed: int = 0
    removal_checked: bool = False
    employers_new: int = 0
    dup_groups: int = 0
    dup_postings: int = 0
    dup_changed: int = 0
    suspicious: int = 0
    notes: list[str] = field(default_factory=list)


def content_hash(rec: PostingRecord) -> str:
    data = json.dumps([getattr(rec, f) for f in WATCHED_FIELDS], ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


def keep(rec: PostingRecord, cfg: SearchConfig) -> bool:
    if rec.area == "remote":
        return cfg.area.include_remote
    if rec.area in ("other_bc", "outside"):
        return cfg.area.keep_other_bc
    return True


def source_outcome(result: SweepResult) -> tuple[str, str | None]:
    """One outcome for the WorkBC source in run_source_results (plan §11 classes)."""
    errors = [f"{t.term}: {t.error}" for t in result.terms if t.error]
    outcomes = {t.outcome for t in result.terms}
    if result.suspicious_empty:
        return "structural", "every term returned 0 postings"
    for worst in ("restricted", "structural", "transient"):
        if worst in outcomes:
            return worst, "; ".join(errors)[:2000] or None
    return "ok", "; ".join(errors)[:2000] or None


def _source_id(conn: psycopg.Connection) -> int:
    row = conn.execute("SELECT id FROM sources WHERE name = %s", (WORKBC_SOURCE,)).fetchone()
    if row:
        return row[0]
    return conn.execute(
        "INSERT INTO sources (kind, name, url) VALUES ('workbc', %s, %s) RETURNING id",
        (WORKBC_SOURCE, WORKBC_URL),
    ).fetchone()[0]


def _employer_id(conn: psycopg.Connection, rec: PostingRecord, stats: SaveStats) -> int | None:
    if not rec.employer_norm:
        return None
    ats = None if rec.ats in ("custom", "jobbank") else rec.ats
    row = conn.execute(
        """
        INSERT INTO employers (name, name_norm, ats, ats_board, careers_host)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (name_norm) DO UPDATE SET
            last_seen_at = now(),
            ats          = COALESCE(employers.ats, EXCLUDED.ats),
            ats_board    = COALESCE(employers.ats_board, EXCLUDED.ats_board),
            careers_host = COALESCE(employers.careers_host, EXCLUDED.careers_host)
        RETURNING id, (xmax = 0) AS inserted
        """,
        (rec.employer_name, rec.employer_norm, ats, rec.ats_board, rec.link_host),
    ).fetchone()
    if row[1]:
        stats.employers_new += 1
    return row[0]


_POSTING_COLUMNS = (
    "title", "employer_name", "city", "area", "workplace", "date_posted", "last_updated",
    "salary_text", "hours", "period", "is_job_bank", "jump_url", "canonical_url", "ats",
    "ats_board", "ats_posting_id", "matched_terms", "match_quality",
)  # fmt: skip


def _values(rec: PostingRecord) -> dict:
    v = {c: getattr(rec, c) for c in _POSTING_COLUMNS}
    v["matched_terms"] = list(rec.matched_terms)
    return v


def _event(conn, posting_id: int, run_id: int, kind: str, details: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO posting_events (posting_id, run_id, kind, details) VALUES (%s, %s, %s, %s)",
        (posting_id, run_id, kind, Jsonb(details or {})),
    )


def _save_posting(
    conn: psycopg.Connection,
    rec: PostingRecord,
    run_id: int,
    source_id: int,
    stats: SaveStats,
    all_terms: bool,
) -> None:
    employer_id = _employer_id(conn, rec, stats)
    values = _values(rec)
    digest = content_hash(rec)
    old = conn.execute(
        f"SELECT id, status, content_hash, matched_terms, {', '.join(WATCHED_FIELDS)}"
        " FROM postings WHERE workbc_job_id = %s FOR UPDATE",
        (rec.workbc_job_id,),
    ).fetchone()

    if old is None:
        cols = ["workbc_job_id", "found_via", "source_id", "employer_id", "content_hash", *values]
        params = [rec.workbc_job_id, "workbc", source_id, employer_id, digest, *values.values()]
        posting_id = conn.execute(
            f"INSERT INTO postings ({', '.join(cols)})"
            f" VALUES ({', '.join(['%s'] * len(cols))}) RETURNING id",
            params,
        ).fetchone()[0]
        _event(conn, posting_id, run_id, "new", {"terms": list(rec.matched_terms)})
        stats.new += 1
        return

    posting_id, status, old_hash, old_terms = old[0], old[1], old[2], old[3] or []
    if not all_terms:  # a --term run only saw some terms: add to the list, don't replace it
        values["matched_terms"] = old_terms + [t for t in rec.matched_terms if t not in old_terms]
    # A board found later by enrichment (e.g. for ?gh_jid= links) is kept when the WorkBC
    # link alone doesn't name one.
    sets = ", ".join(
        f"{c} = COALESCE(%s, {c})" if c == "ats_board" else f"{c} = %s" for c in values
    )
    conn.execute(
        f"UPDATE postings SET {sets}, employer_id = %s, content_hash = %s, status = 'active',"
        " last_seen_at = now(), missed_runs = 0 WHERE id = %s",
        [*values.values(), employer_id, digest, posting_id],
    )
    if status == "removed":
        _event(conn, posting_id, run_id, "reappeared")
        stats.reappeared += 1
    if old_hash != digest:
        before = dict(zip(WATCHED_FIELDS, old[4:], strict=True))
        diff = {
            f: [before[f], getattr(rec, f)] for f in WATCHED_FIELDS if before[f] != getattr(rec, f)
        }
        _event(conn, posting_id, run_id, "changed", diff)
        stats.changed += 1
    elif status != "removed":
        stats.seen_again += 1


def _mark_missed(
    conn: psycopg.Connection, seen_ids: list[str], run_id: int, stats: SaveStats
) -> None:
    missed = conn.execute(
        """
        UPDATE postings SET missed_runs = missed_runs + 1
        WHERE found_via = 'workbc' AND status = 'active'
          AND NOT (workbc_job_id = ANY(%s))
        RETURNING id, missed_runs
        """,
        (seen_ids,),
    ).fetchall()
    stats.marked_missed = len(missed)
    gone = [pid for pid, misses in missed if misses >= MISSES_BEFORE_REMOVED]
    if gone:
        conn.execute("UPDATE postings SET status = 'removed' WHERE id = ANY(%s)", (gone,))
        for pid in gone:
            _event(conn, pid, run_id, "removed", {"missed_runs": MISSES_BEFORE_REMOVED})
    stats.removed = len(gone)


def save_sweep(
    conn: psycopg.Connection,
    result: SweepResult,
    records: list[PostingRecord],
    cfg: SearchConfig,
    *,
    all_terms: bool,
) -> SaveStats:
    """Write one sweep in a single transaction. `all_terms`: every configured term was run."""
    stats = SaveStats()
    outcome, error = source_outcome(result)
    terms_ok = sum(1 for t in result.terms if t.ok and t.complete)
    with conn.transaction():
        source_id = _source_id(conn)
        run_id = conn.execute(
            "INSERT INTO scout_runs (kind, ok, terms_ok, terms_failed, notes)"
            " VALUES ('sweep', %s, %s, %s, %s) RETURNING id",
            (
                result.complete,
                terms_ok,
                len(result.terms) - terms_ok,
                None
                if all_terms
                else "subset of terms: " + ", ".join(t.term for t in result.terms),
            ),
        ).fetchone()[0]
        stats.run_id = run_id

        for rec in records:
            if keep(rec, cfg):
                _save_posting(conn, rec, run_id, source_id, stats, all_terms)
            else:
                stats.skipped_area += 1

        if result.complete and all_terms:
            _mark_missed(conn, [r.workbc_job_id for r in records], run_id, stats)
            stats.removal_checked = True
        elif not all_terms:
            stats.notes.append("only some terms were run: no posting counted as missed")
        else:
            stats.notes.append("sweep incomplete: no posting counted as missed")

        conn.execute(
            "INSERT INTO run_source_results (run_id, source_id, outcome, count, error)"
            " VALUES (%s, %s, %s, %s, %s)",
            (run_id, source_id, outcome, len(records), error),
        )
        if outcome == "ok":
            conn.execute(
                "UPDATE sources SET last_success_at = now(), consecutive_failures = 0,"
                " status = CASE WHEN status = 'degraded' THEN 'active' ELSE status END"
                " WHERE id = %s",
                (source_id,),
            )
        else:
            conn.execute(
                "UPDATE sources SET consecutive_failures = consecutive_failures + 1,"
                " status = CASE WHEN consecutive_failures + 1 >= 3 AND status = 'active'"
                " THEN 'degraded' ELSE status END WHERE id = %s",
                (source_id,),
            )
        regroup(conn, stats)
        stats.suspicious, _ = trust.rescore(conn)
        conn.execute("UPDATE scout_runs SET finished_at = now() WHERE id = %s", (run_id,))
    return stats


def regroup(conn: psycopg.Connection, stats: SaveStats | None = None) -> SaveStats:
    """Recompute dup_group_id for every posting (task 1.7). Cheap: a few hundred rows."""
    stats = stats or SaveStats()
    rows = conn.execute(
        "SELECT p.id, p.canonical_url, e.name_norm, p.title, p.first_seen_at, p.jump_url,"
        " p.dup_group_id FROM postings p LEFT JOIN employers e ON e.id = p.employer_id"
    ).fetchall()
    items = [dupes.Item(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]
    new = dupes.groups(items)
    changed = [(new.get(r[0]), r[0]) for r in rows if new.get(r[0]) != r[6]]
    if changed:
        with conn.cursor() as cur:
            cur.executemany("UPDATE postings SET dup_group_id = %s WHERE id = %s", changed)
    stats.dup_groups = len(set(new.values()))
    stats.dup_postings = len(new)
    stats.dup_changed = len(changed)
    return stats
