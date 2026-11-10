status: open
branch: agent/0003-amazon

# 0003: Amazon's career site in enrichment

**Goal:** Scout reads Amazon's own career site, `www.amazon.jobs`. It's the largest site
Scout can't read yet: `boards.csv` shows 14 active WorkBC postings that link there, none with
details. Like the other boards:
- those WorkBC postings get a description, location and workplace;
- other matching Amazon jobs in Metro Vancouver, or remote in Canada, are saved as board-only
  jobs.

This may take more than one session. Commit after each step and keep `handoff.md` current.

**What we know:**
- **Links** look like `https://www.amazon.jobs/en/jobs/10460889`, sometimes with a slug after
  the id. Today `links.py` classifies them as `custom` with no board or posting id.
  - Add a rule: `ats` is `amazon`, board `amazon`, posting id the numeric id.
  - Keep `canonical_url` exactly as it is today for these links, because duplicate detection
    uses it.
  - The `ats` column is free text (no migration needed). Each sweep rewrites a posting's
    `ats` from its link, so existing postings switch to `amazon` on the next sweep.
- **The search API:** the site's search page loads its results from a JSON endpoint. Find it by
  looking at what the page requests; it's commonly `/en/search.json` with parameters for
  keywords, location, offset and page size. It's unofficial, so check real responses yourself.
- **Check `robots.txt` first.** If it disallows the search endpoint, stop: don't use it, and
  write "amazon: restricted by robots.txt" under "Needs your attention". That ends the task;
  Claude will pick another site.
- **Bound the requests as for Workday:** one search per configured term, limited to the Vancouver
  area and to Canada, with all pages of each search. The list must be the same set on every
  run, because `_mark_board_missed` treats it as complete. Fetch a job's page only if the
  search results lack what Scout needs. Space requests 1 second apart.
- **Lesson from task 0002:** skip a single unusable result item (and count it) rather than
  failing the board. Fail only when the response shape is wrong, or when every item is
  unusable.
- **Failure outcomes** as for the other boards: 401/403 → `restricted`, 404 or an unexpected
  shape → `structural`, timeouts/429/5xx → `transient`.

**Decisions (2026-11-10, answers to your handoff after session 1):**
- Your discovery was good. Next session, **start coding within your first 10 turns**, with at
  most 3 more discovery requests in total.
- **Location:** search per configured term with `location[]=vancouvervictoria-canada`, all
  pages. Scout's own area check then keeps Metro Vancouver and drops Victoria; don't filter
  by city yourself.
- **Remote in Canada:** try your options (b) and (c) with at most 3 requests. If neither gives
  Canadian remote jobs, **leave remote out** and say so in the module docstring. Don't run
  global searches filtered on our side: they're unbounded, as you found.
- **WorkBC postings not in those results** (`keep_ids`): if the job page loads its data from a
  per-job JSON URL, fetch those ids one by one (1 request each, 1 second apart). Find out
  with at most 2 requests. If there's no such URL, leave them unmatched and say how many.
- Use `search.json`'s `description` (it's already in the results), clipped like the other
  boards. No job-page requests for jobs that are in the search results.
- `posted_date` like "November  9, 2026" → a date. Workplace is `remote` only when the job's
  location says so, otherwise `unknown`.

**Allowed paths:** the usual list, plus `src/scout/cli.py` (only the `enrich` help text).

**Done when:**
- `amazon` is in `SUPPORTED`, and `links.py` has the Amazon rule. There are tests for both
  link forms, with the canonical URL unchanged.
- An Amazon board gives `BoardPosting`s with title, link, posting id, location, workplace
  when the site says it, description (plain text, clipped) and published date when available.
- There are tests with synthetic fixtures (made-up ids and titles) for: the parser, pagination,
  the search terms used, skipping a bad item, and each failure outcome. All existing tests pass
  unchanged.
- Your handoff gives a dry run from `scratch/`: requests made, jobs found, and how many of
  `boards.csv`'s 14 Amazon postings it would match.

**Hints:**
- Start your branch from `origin/main`: `git -C /workspace/repo switch -c agent/0003-amazon
  origin/main`.
- Follow how Workday plugs in (`_fetch_workday` and the `workday` branch in `fetch_board`);
  Amazon needs no host from the database.
- `boards.csv` has quoted commas: read it with Python's `csv` module.
