"""Command line entry point: `scout <command>`.

  scout smoke [URL]   Load a real page in Chromium, save markdown + metadata as JSON.
  scout sample        Extract jobs from examples/sample_careers.html, save as JSON.
  scout db migrate    Apply new migrations/ files to Postgres (as scout_migrate).
  scout db status     List applied and pending migrations.
  scout db check      Connect as each role and check what it can and can't do.
  scout sweep [--dry-run] [--term T ...] [--no-save] [--replay SWEEP]
                      Search WorkBC for every term in config/search.json, classify the
                      postings, print a summary and save them to the database.
                      --dry-run writes nothing to the database; --replay (with
                      --dry-run) re-runs the rules on a saved sweep, offline. After
                      saving, the job boards are read too (--no-enrich to skip).
  scout status        What's in the database: counts, recent runs, problems, newest jobs.
  scout enrich [--dry-run] [--board ATS:BOARD ...]
                      Read the Ashby, Greenhouse and Lever boards of saved postings:
                      details for those postings, plus matching jobs only on the boards.

Add --show-browser to either command to run Chromium with a visible window
(only works where there's a display, i.e. not in Codespaces).
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path

# Browser code (crawl4ai, Chromium) is imported inside the commands that need it, so the
# slim runtime image (no browser) can still run `scout db ...`.

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = REPO_ROOT / "output"
SAMPLE_PAGE = REPO_ROOT / "examples" / "sample_careers.html"

# A JavaScript-rendered practice site built for scraping exercises: its quotes only
# appear after JS runs, so a successful result proves the browser is working.
DEFAULT_SMOKE_URL = "https://quotes.toscrape.com/js/"


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


async def smoke(url: str, headless: bool) -> int:
    from scout.crawl import fetch_page
    from scout.writers import write_json

    result = await fetch_page(url, headless=headless)
    if not result.success:
        print(f"FAILED: {result.error_message}")
        return 1

    markdown = str(result.markdown or "")
    record = {
        "url": result.url,
        "status_code": result.status_code,
        "title": (result.metadata or {}).get("title"),
        "markdown_chars": len(markdown),
        "internal_links": len(result.links.get("internal", [])),
        "external_links": len(result.links.get("external", [])),
        "markdown": markdown,
    }
    path = write_json(record, OUTPUT_DIR / f"smoke-{_stamp()}.json")
    print(f"OK: {record['title']!r}, {len(markdown)} chars of markdown -> {path}")
    return 0


async def sample(headless: bool) -> int:
    from scout.crawl import extract_jobs
    from scout.extract import SAMPLE_CAREERS_SCHEMA
    from scout.writers import write_postings

    url = SAMPLE_PAGE.as_uri()  # file:///.../examples/sample_careers.html
    postings = await extract_jobs(
        url,
        SAMPLE_CAREERS_SCHEMA,
        company="Example Corp",
        # Pretend the page lives on a real site so relative links resolve sensibly.
        base_url="https://careers.example.com/",
        headless=headless,
    )
    path = write_postings(postings, OUTPUT_DIR / f"sample-jobs-{_stamp()}.json")
    for p in postings:
        print(f"  - {p.title} ({p.location}) {p.url}")
    print(f"OK: {len(postings)} postings -> {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scout")
    parser.add_argument("--show-browser", action="store_true", help="run Chromium with a window")
    sub = parser.add_subparsers(dest="command", required=True)

    p_smoke = sub.add_parser("smoke", help="crawl one real page and save it as JSON")
    p_smoke.add_argument("url", nargs="?", default=DEFAULT_SMOKE_URL)

    sub.add_parser("sample", help="extract jobs from the bundled sample careers page")

    p_db = sub.add_parser("db", help="database: migrate, status, check")
    p_db.add_argument("action", choices=["migrate", "status", "check"])

    p_sweep = sub.add_parser("sweep", help="search WorkBC for every configured term")
    p_sweep.add_argument(
        "--dry-run", action="store_true", help="fetch and summarize; don't write to the database"
    )
    p_sweep.add_argument(
        "--term", action="append", dest="terms", help="only this term (repeatable)"
    )
    p_sweep.add_argument("--no-save", action="store_true", help="don't save raw responses")
    p_sweep.add_argument(
        "--replay", metavar="SWEEP", help="use a saved sweep (e.g. sweep-20261031-065758Z)"
    )
    p_sweep.add_argument("--no-enrich", action="store_true", help="don't read job boards after")

    sub.add_parser("status", help="what's in the database, at a glance")

    p_enrich = sub.add_parser("enrich", help="read Ashby, Greenhouse and Lever boards")
    p_enrich.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    p_enrich.add_argument(
        "--board",
        action="append",
        dest="boards",
        metavar="ATS:BOARD",
        help="only this board, e.g. lever:fabrikam (repeatable)",
    )

    args = parser.parse_args(argv)

    if args.command == "db":
        return run_db(args.action)
    if args.command == "sweep":
        from scout import sweep

        return sweep.run(
            args.dry_run,
            terms=args.terms,
            save_raw=not args.no_save,
            replay=args.replay,
            enrich_after=not args.no_enrich,
        )
    if args.command == "enrich":
        return run_enrich(args.dry_run, args.boards)
    if args.command == "status":
        from scout import status

        return status.run()
    headless = not args.show_browser

    if args.command == "smoke":
        return asyncio.run(smoke(args.url, headless))
    if args.command == "sample":
        return asyncio.run(sample(headless))
    return 2


def run_enrich(dry_run: bool, boards: list[str] | None) -> int:
    from scout import config, db, enrich

    try:
        cfg = config.load()
        conn = db.connect("reader" if dry_run else "ingest", autocommit=True)
    except (config.ConfigError, db.DbError) as e:
        print(f"ERROR: {e}")
        return 1
    with conn:
        stats = enrich.enrich(conn, cfg, dry_run=dry_run, only=boards)
    enrich.report(stats, dry_run=dry_run)
    return 0 if all(b.outcome in ("ok", "empty") for b in stats.boards) else 1


def run_db(action: str) -> int:
    from scout import db

    try:
        return {"migrate": db.migrate, "status": db.status, "check": db.check}[action]()
    except db.DbError as e:
        print(f"ERROR: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
