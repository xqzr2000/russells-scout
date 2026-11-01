"""`scout status`: what's in the database, at a glance (read-only, as scout_reader).

Shows posting counts, what's relevant now, job-board details, duplicates, trust, the most
recent runs, sources that need attention and the newest relevant jobs. Times are shown in
$SCOUT_TZ (default America/Vancouver).
"""

from __future__ import annotations

import os

from scout import db


def _money(x) -> str:
    if x is None:
        return "?"
    x = float(x)
    return f"{x:,.0f}" if x >= 1000 else f"{x:g}"


def run(out=print, limit: int = 10) -> int:
    tz = os.environ.get("SCOUT_TZ", "America/Vancouver")
    try:
        conn = db.connect("reader")
    except db.DbError as e:
        out(f"ERROR: {e}")
        return 1
    with conn:

        def one(sql, *params):
            return conn.execute(sql, params).fetchone()

        def rows(sql, *params):
            return conn.execute(sql, params).fetchall()

        active, workbc, boards, removed = one(
            "SELECT count(*) FILTER (WHERE status = 'active'),"
            " count(*) FILTER (WHERE status = 'active' AND found_via = 'workbc'),"
            " count(*) FILTER (WHERE status = 'active' AND found_via = 'ats'),"
            " count(*) FILTER (WHERE status = 'removed') FROM postings"
        )
        relevant = one(
            "SELECT count(*) FROM postings WHERE status = 'active'"
            " AND area IN ('metro', 'remote') AND match_quality = 'exact'"
        )[0]
        new7 = one("SELECT count(*) FROM v_new_relevant_7d")[0]
        review = one("SELECT count(*) FROM v_review")[0]
        areas = rows(
            "SELECT area, count(*) FROM postings WHERE status = 'active' GROUP BY 1 ORDER BY 2 DESC"
        )
        enriched, with_pay = one(
            "SELECT count(*) FILTER (WHERE enriched_at IS NOT NULL),"
            " count(*) FILTER (WHERE pay_min IS NOT NULL OR pay_max IS NOT NULL)"
            " FROM postings WHERE status = 'active'"
        )
        groups, grouped = one(
            "SELECT count(DISTINCT dup_group_id), count(dup_group_id) FROM postings"
            " WHERE status = 'active'"
        )
        scores = rows(
            "SELECT trust_score, count(*) FROM postings WHERE status = 'active'"
            " GROUP BY 1 ORDER BY 1"
        )
        suspicious = one("SELECT count(*) FROM v_suspicious WHERE status = 'active'")[0]

        out("== postings")
        out(f"  active {active} (WorkBC {workbc}, found on job boards {boards}); removed {removed}")
        out(f"  relevant now (Metro Vancouver or remote, strong title match): {relevant}")
        out(f"  new in the last 7 days: {new7}   possible matches to review: {review}")
        out("  by area: " + (", ".join(f"{a} {n}" for a, n in areas) or "-"))
        out(f"  with job-board details: {enriched} (with pay {with_pay})")
        out(f"  likely duplicates: {groups} group(s), {grouped} postings")
        out("  trust scores: " + (", ".join(f"{s:+d}: {n}" for s, n in scores if s is not None)
                                  or "-"))  # fmt: skip
        out(f"  suspicious (below 0): {suspicious}")

        low = rows(
            "SELECT title, COALESCE(e.name, p.employer_name), trust_score, trust_reasons"
            " FROM postings p LEFT JOIN employers e ON e.id = p.employer_id"
            " WHERE p.status = 'active' AND trust_score <= 0"
            " ORDER BY trust_score, p.id LIMIT %s",
            limit,
        )
        if low:
            out("")
            out("== lowest trust (0 or below)")
            for title, employer, score, reasons in low:
                out(f"  {score:+d} {title} | {employer} | {', '.join(reasons) or 'no signals'}")

        out("")
        out(f"== recent runs ({tz})")
        for rid, kind, started, ok, t_ok, t_failed, notes in rows(
            "SELECT id, kind, to_char(started_at AT TIME ZONE %s, 'YYYY-MM-DD HH24:MI'), ok,"
            " terms_ok, terms_failed, notes FROM scout_runs ORDER BY id DESC LIMIT 6",
            tz,
        ):
            what = "terms" if kind == "sweep" else "boards"
            result = "ok" if ok else ("running" if ok is None else "NOT OK")
            out(f"  #{rid} {started} {kind:6} {result:7} {what} ok {t_ok}, failed {t_failed}"
                + (f"  ({notes})" if notes else ""))  # fmt: skip

        last_digest = one(
            "SELECT to_char(made_at AT TIME ZONE %s, 'YYYY-MM-DD HH24:MI'), new_relevant,"
            " problems, file FROM digests ORDER BY id DESC LIMIT 1",
            tz,
        )
        if last_digest:
            when, new, problems, file = last_digest
            out(f"  last digest {when}: {new} new job(s), {problems} problem(s) ({file})")
        else:
            out("  no digest written yet")

        trouble = rows(
            "SELECT name, status, consecutive_failures, last_outcome, last_error"
            " FROM v_source_health WHERE status <> 'active' OR consecutive_failures > 0"
        )
        out("")
        out("== sources needing attention" + ("" if trouble else ": none"))
        for name, status, fails, outcome, error in trouble:
            out(f"  {name}: {status}, {fails} failure(s) in a row, last {outcome}"
                + (f": {error}" if error else ""))  # fmt: skip

        newest = rows(
            "SELECT title, employer, COALESCE(city, ''), workplace, pay_min, pay_max,"
            " pay_currency, pay_period FROM v_new_relevant_7d LIMIT %s",
            limit,
        )
        if newest:
            out("")
            out("== newest relevant jobs")
            for title, employer, city, workplace, lo, hi, cur, period in newest:
                pay = ""
                if lo is not None or hi is not None:
                    pay = f" | {cur or ''} {_money(lo)}–{_money(hi)}"
                    pay += f"/{period}" if period and period != "year" else ""
                where = city if workplace == "unknown" else f"{city} ({workplace})"
                out(f"  {title} | {employer} | {where}{pay}")
    return 0
