status: done
branch: agent/0000-orientation

# 0000: Orientation

**Goal:** a first, short session that checks your setup and reads the code you'll work on
next. No new scouting code and no web requests.

**Steps:**
1. Read `/briefing/session.txt`, `/briefing/status.txt` and the first 20 lines of
   `/briefing/boards.csv`.
2. Set up `/workspace/.venv` (see the Tests rules). Run
   `tests/test_ats.py tests/test_links.py tests/test_enrich.py` and note the result.
3. Read how a job board plugs into enrichment: the module docstring and `BoardPosting` in
   `src/scout/sources/ats.py`, `SUPPORTED`, and how `src/scout/enrich.py` calls the fetchers.
   Use `grep -n` and line ranges, not whole files.
4. On the branch `agent/0000-orientation`, add `notes/orientation.md` (at most 40 lines):
   - how a new ATS would plug in, naming the functions and the files to touch;
   - the test result;
   - how many active postings `boards.csv` shows on Workday boards, across how many boards;
   - anything in the setup that didn't work.

   Commit it.
5. Write `handoff.md` and a journal line as the rules say.

**Allowed paths:** `notes/orientation.md` in the repo, for this task only. This branch is
never merged.

**Done when:** the branch has one commit with `notes/orientation.md`, and `handoff.md` starts
with the rules line.
