status: open
branch: agent/0002-workday-bad-items

# 0002: Repair: one bad Workday item must not fail the whole board

**What happened:** on 2026-11-08, the 06:41 enrichment run failed one board:

```
workday:accenture/AccentureCareers | structural | unexpected response: posting 4 has no title or externalPath
```

One item in Accenture's search results had no `title` or `externalPath`. `_workday_list_item`
raises `ValueError`, and `_fetch_workday` turns that into a `structural` result with no
postings. So all of Accenture's good postings were skipped for that run. The 16:41 run was
fine: Workday sometimes returns an odd item.

**Goal:** an unusable item is skipped, not fatal.
- An item with no title, no `externalPath` or no posting id is skipped and counted. The board
  stays `ok` with the remaining postings.
- The board is still `structural` when the response itself has the wrong shape (not JSON, no
  `jobPostings` list), and when **every** item on a page is unusable. That points to a real
  format change, which we want to hear about.
- Make the skips visible in the enrich report if there's a natural place for it (for example
  `BoardStats`, which the report prints). Don't change the database.

**Why skipping is safe:** a skipped item's posting looks missing for that one run. A posting
is only marked `removed` after 3 misses **and** 24 hours unseen, so an occasional odd item
can't close a job.

**Allowed paths:** the usual list.

**Done when:**
- There's a synthetic fixture: a Workday search page with one usable and one unusable item,
  plus a page where every item is unusable. Tests show:
  - the first gives `ok` with the good posting and a skip count;
  - the second gives `structural`.
- All existing tests pass unchanged. In particular, `test_workday_unexpected_response_is_structural`
  still holds for a non-JSON body and for a response with no `jobPostings`.
- Your handoff says what you changed and how the skip count shows up.

**Hints:**
- Start your branch from `origin/main`: `git -C /workspace/repo switch -c
  agent/0002-workday-bad-items origin/main`. Don't build on another `agent/*` branch, because
  your Workday work is already in `main`.
- This is a small change: the loop over `items` in `_fetch_workday` and `_workday_list_item` in
  `src/scout/sources/ats.py`. No web requests are needed.
