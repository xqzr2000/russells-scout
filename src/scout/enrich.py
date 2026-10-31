"""Job-board enrichment: Ashby, Greenhouse, Lever (docs/phase1-plan.md §3 step 7, task 1.8).

`scout enrich` (and `scout sweep`, after saving) does, as scout_ingest:

1. Board names known for an employer are copied to its postings that lack one.
2. Greenhouse postings whose link carries only ?gh_jid=: a few board names made from the
   employer's name are tried, and the one where that exact job exists is kept (employer
   re-tried at most weekly).
3. Every Ashby, Greenhouse and Lever board with an active posting is fetched (one request
   each, one second apart). Per board, in its own transaction (a failing board never
   affects the others):
     - WorkBC postings found on the board get its description, pay range, workplace and
       location (`enriched_at` set);
     - other postings on the board that match a search term (all its words in the title, as
       WorkBC's search does) and are in Metro Vancouver or remote in Canada are saved with
       found_via = 'ats'
       (`new` / `reappeared` / `changed` events, like a sweep);
     - board-found postings no longer on a board that answered are counted as missed and
       removed after 3 misses.
4. Duplicate groups are recomputed.

`--dry-run` reads as scout_reader and writes nothing.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

import psycopg
from psycopg.types.json import Jsonb

from scout import store, trust
from scout.config import SearchConfig
from scout.pipeline import classify
from scout.pipeline.links import canonical_url
from scout.sources import ats as ats_api
from scout.sources.ats import BoardPosting, BoardResult

BOARD_RECHECK = "7 days"
# Remote jobs count only in Canada: the location names Canada or a province or city in it,
# or says nothing but "remote" (and the board gives no other country).
_CANADA = re.compile(
    r"\b(canada|canadian|bc|b\.c\.|british columbia|vancouver|alberta|ontario|quebec|québec"
    r"|manitoba|saskatchewan|nova scotia|new brunswick|newfoundland|prince edward island|yukon"
    r"|nunavut|northwest territories|toronto|montreal|montréal|calgary|ottawa|edmonton"
    r"|winnipeg|halifax)\b"
)
_JUST_REMOTE = re.compile(r"\b(remote|anywhere|worldwide|global|distributed|work from home|wfh)\b")


@dataclass
class BoardStats:
    ats: str
    board: str
    outcome: str = "ok"
    error: str | None = None
    on_board: int = 0
    matched: int = 0
    with_pay: int = 0
    with_description: int = 0
    with_workplace: int = 0
    new: int = 0
    seen_again: int = 0
    reappeared: int = 0
    changed: int = 0
    missed: int = 0
    removed: int = 0
    candidates: list[str] = field(default_factory=list)  # dry run: titles that would be added


@dataclass
class EnrichStats:
    run_id: int | None = None
    discovered: list[tuple[str, str]] = field(default_factory=list)  # (employer, board)
    not_found: list[str] = field(default_factory=list)
    boards: list[BoardStats] = field(default_factory=list)
    dup_groups: int = 0
    suspicious: int = 0


def ats_area(bp: BoardPosting, metro) -> str:
    """metro, remote (in Canada or unstated), or outside (not kept)."""
    if bp.city and classify.norm_city(bp.city) in {classify.norm_city(m) for m in metro}:
        return "metro"
    loc = (bp.location or "").casefold()
    remote = bp.workplace == "remote" or "remote" in loc
    if not remote:
        return "outside"
    if _CANADA.search(loc):
        return "remote"
    leftover = re.sub(r"[^a-z]+", " ", _JUST_REMOTE.sub(" ", loc)).strip()
    country_ok = not bp.country or bp.country.casefold() in ("ca", "can", "canada")
    return "remote" if not leftover and country_ok else "outside"


def _keep_area(area: str, cfg: SearchConfig) -> bool:
    return area == "metro" or (area == "remote" and cfg.area.include_remote)


def _fill_boards_from_employers(conn: psycopg.Connection) -> None:
    conn.execute(
        "UPDATE postings p SET ats_board = e.ats_board FROM employers e"
        " WHERE p.employer_id = e.id AND p.ats_board IS NULL AND p.ats = e.ats"
        " AND e.ats_board IS NOT NULL"
    )


def _discover_greenhouse(conn, stats: EnrichStats, *, dry_run, get, sleep, delay) -> None:
    rows = conn.execute(
        f"""
        SELECT e.id, e.name, e.name_norm, e.careers_host, min(p.ats_posting_id)
        FROM postings p JOIN employers e ON e.id = p.employer_id
        WHERE p.ats = 'greenhouse' AND p.ats_board IS NULL AND p.status = 'active'
          AND p.ats_posting_id IS NOT NULL
          AND (e.board_checked_at IS NULL OR e.board_checked_at < now() - interval '{BOARD_RECHECK}')
        GROUP BY e.id ORDER BY e.name
        """
    ).fetchall()
    for n, (emp_id, name, norm, host, gh_jid) in enumerate(rows):
        if n:
            sleep(delay)
        candidates = ats_api.greenhouse_board_candidates(name, norm, host)
        board = ats_api.find_greenhouse_board(gh_jid, candidates, get=get, sleep=sleep, delay=delay)
        if board:
            stats.discovered.append((name, board))
        else:
            stats.not_found.append(name)
        if dry_run:
            continue
        with conn.transaction():
            conn.execute(
                "UPDATE employers SET board_checked_at = now(),"
                " ats = COALESCE(%s, ats), ats_board = COALESCE(%s, ats_board) WHERE id = %s",
                ("greenhouse" if board else None, board, emp_id),
            )
            if board:
                conn.execute(
                    "UPDATE postings SET ats_board = %s WHERE employer_id = %s"
                    " AND ats = 'greenhouse' AND ats_board IS NULL",
                    (board, emp_id),
                )


def _boards(conn, only: list[str] | None, extra: list[tuple[str, str]]) -> list[tuple[str, str]]:
    rows = conn.execute(
        "SELECT DISTINCT ats, ats_board FROM postings WHERE status = 'active'"
        " AND ats = ANY(%s) AND ats_board IS NOT NULL ORDER BY 1, 2",
        (list(ats_api.SUPPORTED),),
    ).fetchall()
    boards = list(dict.fromkeys([(a, b) for a, b in rows] + extra))
    if only:
        wanted = {tuple(o.split(":", 1)) for o in only}
        boards = [b for b in boards if b in wanted] + [w for w in wanted if w not in boards]
    return boards


def _employer_for_board(conn, ats: str, board: str) -> tuple[int | None, str | None]:
    row = conn.execute(
        "SELECT id, name FROM employers WHERE ats = %s AND lower(ats_board) = lower(%s)"
        " ORDER BY id LIMIT 1",
        (ats, board),
    ).fetchone()
    return (row[0], row[1]) if row else (None, None)


def _match_workbc(conn, bp: BoardPosting, bs: BoardStats, dry_run: bool) -> bool:
    if dry_run:
        found = conn.execute(
            "SELECT count(*) FROM postings WHERE found_via = 'workbc' AND ats = %s"
            " AND (lower(ats_board) = lower(%s) OR ats_board IS NULL) AND ats_posting_id = %s",
            (bp.ats, bp.board, bp.posting_id),
        ).fetchone()[0]
    else:
        found = conn.execute(
            """
            UPDATE postings SET description = %s, pay_min = %s, pay_max = %s,
                pay_currency = %s, pay_period = %s, ats_workplace = %s, location_text = %s,
                enriched_at = now()
            WHERE found_via = 'workbc' AND ats = %s
              AND (lower(ats_board) = lower(%s) OR ats_board IS NULL)  -- posting ids are unique per ATS
              AND ats_posting_id = %s
            """,
            (
                bp.description, bp.pay_min, bp.pay_max, bp.pay_currency, bp.pay_period,
                bp.workplace, bp.location, bp.ats, bp.board, bp.posting_id,
            ),
        ).rowcount  # fmt: skip
    if not found:
        return False
    bs.matched += 1
    bs.with_pay += bp.pay_min is not None or bp.pay_max is not None
    bs.with_description += bool(bp.description)
    bs.with_workplace += bp.workplace != "unknown"
    return True


def _save_board_posting(conn, bp, bs, cfg, *, run_id, source_id, employer, dry_run) -> str | None:
    """Save a posting found only on the board, if it matches the search. Returns its id."""
    hits = classify.term_hits(bp.title, cfg.terms)
    area = ats_area(bp, cfg.area.metro)
    if not hits or not _keep_area(area, cfg):
        return None
    if dry_run:
        bs.candidates.append(f"{bp.title} | {bp.location}")
        return bp.posting_id
    emp_id, emp_name = employer
    rec = SimpleNamespace(title=bp.title, employer_name=emp_name, city=bp.city, salary_text=None)
    values = {
        "title": bp.title, "employer_id": emp_id, "employer_name": emp_name, "city": bp.city,
        "area": area, "workplace": bp.workplace, "ats_workplace": bp.workplace,
        "location_text": bp.location, "date_posted": bp.published_at, "jump_url": bp.url,
        "canonical_url": canonical_url(bp.url) if bp.url else None, "description": bp.description,
        "pay_min": bp.pay_min, "pay_max": bp.pay_max, "pay_currency": bp.pay_currency,
        "pay_period": bp.pay_period, "matched_terms": list(hits),
        "match_quality": classify.title_match(bp.title, cfg.terms).quality,
        "content_hash": store.content_hash(rec),
    }  # fmt: skip
    old = conn.execute(
        "SELECT id, status, content_hash, title, employer_name, city FROM postings"
        " WHERE found_via = 'ats' AND ats = %s AND ats_board = %s AND ats_posting_id = %s"
        " FOR UPDATE",
        (bp.ats, bp.board, bp.posting_id),
    ).fetchone()
    if old is None:
        cols = ["found_via", "source_id", "ats", "ats_board", "ats_posting_id", *values]
        params = ["ats", source_id, bp.ats, bp.board, bp.posting_id, *values.values()]
        pid = conn.execute(
            f"INSERT INTO postings ({', '.join(cols)}, enriched_at)"
            f" VALUES ({', '.join(['%s'] * len(cols))}, now()) RETURNING id",
            params,
        ).fetchone()[0]
        _event(conn, pid, run_id, "new", {"terms": list(hits), "board": f"{bp.ats}:{bp.board}"})
        bs.new += 1
        return bp.posting_id
    pid, status, old_hash = old[0], old[1], old[2]
    sets = ", ".join(f"{c} = %s" for c in values)
    conn.execute(
        f"UPDATE postings SET {sets}, status = 'active', last_seen_at = now(), missed_runs = 0,"
        " enriched_at = now() WHERE id = %s",
        [*values.values(), pid],
    )
    if status == "removed":
        _event(conn, pid, run_id, "reappeared")
        bs.reappeared += 1
    if old_hash != values["content_hash"]:
        before = dict(zip(("title", "employer_name", "city"), old[3:], strict=True))
        diff = {f: [before[f], values[f]] for f in before if before[f] != values[f]}
        _event(conn, pid, run_id, "changed", diff)
        bs.changed += 1
    elif status != "removed":
        bs.seen_again += 1
    return bp.posting_id


def _event(conn, posting_id, run_id, kind, details=None):
    conn.execute(
        "INSERT INTO posting_events (posting_id, run_id, kind, details) VALUES (%s, %s, %s, %s)",
        (posting_id, run_id, kind, Jsonb(details or {})),
    )


def _mark_board_missed(conn, res: BoardResult, run_id: int, bs) -> None:
    on_board = [p.posting_id for p in res.postings]
    missed = conn.execute(
        """
        UPDATE postings SET missed_runs = missed_runs + 1
        WHERE found_via = 'ats' AND status = 'active' AND ats = %s AND ats_board = %s
          AND NOT (ats_posting_id = ANY(%s))
        RETURNING id, missed_runs
        """,
        (res.ats, res.board, on_board),
    ).fetchall()
    bs.missed = len(missed)
    gone = [pid for pid, misses in missed if misses >= store.MISSES_BEFORE_REMOVED]
    if gone:
        conn.execute("UPDATE postings SET status = 'removed' WHERE id = ANY(%s)", (gone,))
        for pid in gone:
            _event(conn, pid, run_id, "removed", {"missed_runs": store.MISSES_BEFORE_REMOVED})
    bs.removed = len(gone)


def _source(conn, res: BoardResult) -> int:
    name = f"{res.ats}:{res.board}"
    row = conn.execute("SELECT id FROM sources WHERE name = %s", (name,)).fetchone()
    if row:
        conn.execute("UPDATE sources SET url = %s WHERE id = %s", (res.url, row[0]))
        return row[0]
    return conn.execute(
        "INSERT INTO sources (kind, name, url) VALUES ('ats', %s, %s) RETURNING id",
        (name, res.url),
    ).fetchone()[0]


def _apply_board(conn, res: BoardResult, cfg, run_id, bs: BoardStats, dry_run: bool) -> None:
    source_id = None if dry_run else _source(conn, res)
    employer = _employer_for_board(conn, res.ats, res.board)
    if res.ok:
        for bp in res.postings:
            if not _match_workbc(conn, bp, bs, dry_run):
                _save_board_posting(
                    conn, bp, bs, cfg, run_id=run_id, source_id=source_id, employer=employer,
                    dry_run=dry_run,
                )  # fmt: skip
        if not dry_run:
            _mark_board_missed(conn, res, run_id, bs)
    if dry_run:
        return
    conn.execute(
        "INSERT INTO run_source_results (run_id, source_id, outcome, count, error)"
        " VALUES (%s, %s, %s, %s, %s)",
        (run_id, source_id, res.outcome, len(res.postings), res.error),
    )
    if res.ok:
        conn.execute(
            "UPDATE sources SET last_success_at = now(), consecutive_failures = 0,"
            " status = CASE WHEN status = 'degraded' THEN 'active' ELSE status END WHERE id = %s",
            (source_id,),
        )
    else:
        conn.execute(
            "UPDATE sources SET consecutive_failures = consecutive_failures + 1,"
            " status = CASE WHEN consecutive_failures + 1 >= 3 AND status = 'active'"
            " THEN 'degraded' ELSE status END WHERE id = %s",
            (source_id,),
        )


def enrich(
    conn: psycopg.Connection,
    cfg: SearchConfig,
    *,
    dry_run: bool = False,
    only: list[str] | None = None,
    get: ats_api.Get = ats_api.http_get,
    sleep=time.sleep,
    delay: float = 1.0,
) -> EnrichStats:
    """`conn` must be in autocommit mode (each board gets its own transaction)."""
    stats = EnrichStats()
    if not dry_run:
        _fill_boards_from_employers(conn)
    _discover_greenhouse(conn, stats, dry_run=dry_run, get=get, sleep=sleep, delay=delay)
    extra = [("greenhouse", b) for _, b in stats.discovered] if dry_run else []
    boards = _boards(conn, only, extra)

    if not dry_run:
        stats.run_id = conn.execute(
            "INSERT INTO scout_runs (kind) VALUES ('enrich') RETURNING id"
        ).fetchone()[0]

    for n, (ats, board) in enumerate(boards):
        if n or stats.discovered or stats.not_found:
            sleep(delay)
        res = ats_api.fetch_board(ats, board, get=get, sleep=sleep)
        bs = BoardStats(ats, board, res.outcome, res.error, on_board=len(res.postings))
        if dry_run:
            _apply_board(conn, res, cfg, None, bs, dry_run=True)
        else:
            with conn.transaction():
                _apply_board(conn, res, cfg, stats.run_id, bs, dry_run=False)
        stats.boards.append(bs)

    if not dry_run:
        ok = sum(1 for b in stats.boards if b.outcome in ("ok", "empty"))
        with conn.transaction():
            stats.dup_groups = store.regroup(conn).dup_groups
            stats.suspicious, _ = trust.rescore(conn)
            conn.execute(
                "UPDATE scout_runs SET finished_at = now(), ok = %s, terms_ok = %s,"
                " terms_failed = %s, notes = %s WHERE id = %s",
                (
                    ok == len(stats.boards), ok, len(stats.boards) - ok,
                    f"{len(stats.boards)} job boards", stats.run_id,
                ),
            )  # fmt: skip
    return stats


def report(stats: EnrichStats, out=print, dry_run: bool = False) -> None:
    out("")
    title = "would enrich (dry run)" if dry_run else f"enrichment (run {stats.run_id})"
    out(f"== job boards: {title}")
    if stats.discovered or stats.not_found:
        found = ", ".join(f"{b} ({e})" for e, b in stats.discovered) or "-"
        out(f"  Greenhouse boards found from company links: {found}")
        if stats.not_found:
            out(f"  not found (re-tried in {BOARD_RECHECK}): {', '.join(stats.not_found)}")
    out(f"  {'board':34} {'outcome':10} {'on board':>8} {'matched':>7} {'pay':>4} {'new':>4}  note")
    for b in stats.boards:
        name = f"{b.ats}:{b.board}"
        out(
            f"  {name:34} {b.outcome:10} {b.on_board:>8} {b.matched:>7} {b.with_pay:>4}"
            f" {b.new if not dry_run else len(b.candidates):>4}  {b.error or ''}"
        )
    matched = sum(b.matched for b in stats.boards)
    out(
        f"  WorkBC postings matched on their boards: {matched}"
        f" (description {sum(b.with_description for b in stats.boards)},"
        f" pay {sum(b.with_pay for b in stats.boards)},"
        f" workplace {sum(b.with_workplace for b in stats.boards)})"
    )
    if dry_run:
        cands = [c for b in stats.boards for c in b.candidates]
        out(f"  would add from boards (matching terms, Metro Vancouver or remote): {len(cands)}")
        for c in cands[:15]:
            out(f"    {c}")
    else:
        out(
            f"  from boards: new {sum(b.new for b in stats.boards)},"
            f" seen again {sum(b.seen_again for b in stats.boards)},"
            f" reappeared {sum(b.reappeared for b in stats.boards)},"
            f" missed {sum(b.missed for b in stats.boards)},"
            f" removed {sum(b.removed for b in stats.boards)}"
        )
    if not dry_run:
        out(f"  suspicious postings (trust score below 0): {stats.suspicious}; see v_suspicious")
    failed = [b for b in stats.boards if b.outcome not in ("ok", "empty")]
    if failed:
        out(f"  boards that failed: {len(failed)} (see v_source_health)")
