status: open
branch: agent/0001-workday

# 0001: Workday boards in enrichment

**Goal:** Scout reads Workday job boards the way it reads Ashby, Greenhouse and Lever. That
means:
- WorkBC postings that link to Workday get a description, location and workplace;
- other matching jobs on those boards (Metro Vancouver, or remote in Canada) are saved like
  other board-only jobs.

Workday is the largest group Scout can't read yet: `boards.csv` shows about 17 boards and 42
active postings.

This takes more than one session. Work in steps and commit after each:
1. Explore a few real boards (scratch only).
2. The parser, with synthetic fixtures and tests.
3. The fetcher, with tests using a fake HTTP function.
4. Wiring it into enrichment, with tests.

**What we know** (your orientation notes on `agent/0000-orientation` have the rest):
- Board names are stored as `tenant/site` (`src/scout/pipeline/links.py`, the Workday rule). The
  host's `wdN` number is **not** in the board name. Its full host (for example
  `fabrikam.wd3.myworkdayjobs.com`) is in each posting's link (`boards.csv` `example_link`) and
  in `employers.careers_host`.
  - Don't guess hosts by trying `wd1`, `wd2`, …
  - Don't change how `links.py` names Workday boards: existing rows use `tenant/site`.

  Find a way for enrichment to pass the host along (for example, `enrich._boards` also
  selecting the host for Workday boards).
- The posting id must be the one `links.py` takes from a link: the part after the last `_` in
  the job slug (for example `R123` in `.../job/Vancouver-BC/Data-Scientist_R123`). That's how
  `_match_workbc` finds the WorkBC posting.
- Workday's job search is a JSON `POST` to `https://{host}/wday/cxs/{tenant}/{site}/jobs` with a
  body like `{"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}`. A job's details
  are a `GET` of `https://{host}/wday/cxs/{tenant}/{site}{externalPath}`. This API is
  unofficial, so check the real responses yourself (`robots.txt` first; 1 request a second).
- `fetch_board` and `Get` are GET-only today. Add what Workday needs without changing how the
  other three boards behave. Their existing tests must pass unchanged.
- Big tenants can list hundreds of jobs. **Don't page through a whole board or fetch every
  job's details.** Use `searchText` with the search terms from `config/search.json` (read it
  with `scout.config`), and fetch details only for jobs Scout would keep.
- `_mark_board_missed` treats the list for a board as everything currently open there, so the
  list must be the same set of jobs on every run (the same searches, all their pages). Say
  in your handoff how many requests a typical board takes.
- Map failures the way `fetch_board` already does: 401/403 → `restricted`, 404 or an
  unexpected shape → `structural`, timeouts, 429 and 5xx → `transient`.

**Allowed paths:** the usual list, plus `src/scout/cli.py` (only the `enrich` help text, which
names the supported boards).

**Done when:**
- `workday` is in `SUPPORTED`. A Workday board gives `BoardPosting`s with title, link, posting
  id, location, workplace when Workday says it, description (plain text, clipped like the
  others) and published date when available.
- There are tests with synthetic fixtures (made-up tenant, titles and ids) for: the parser,
  pagination stopping, the search terms used, details fetched only for kept jobs, each failure
  outcome, and enrichment picking up the host. Database tests skip for you; Claude runs them.
- `tests/` passes in your venv, and the existing tests are unchanged.
- Your handoff says, from a dry run in `scratch/` against 2–3 real boards: how many requests
  each took, how many jobs it found, and how many of `boards.csv`'s Workday postings it would
  match.

**Hints:**
- `boards.csv` has quoted commas: read it with Python's `csv` module, not `awk -F,`.
- Read `src/scout/sources/ats.py` and `src/scout/enrich.py` in parts. The functions you'll
  touch are `fetch_board`, `_get_with_retries`, `PARSERS`/`URLS`, `_boards` and `enrich`.
