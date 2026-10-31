# Phase 1 results

Plan: [phase1-plan.md](phase1-plan.md). All runs on the Ubuntu box (PostgreSQL 18.6 in
Docker), 2026-10-30 → 10-31. Numbers are from the command output pasted back each time.

## Exit criteria (phase1-plan.md §7)

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | **Zero tokens:** a sweep makes no LLM calls | **Pass** | The scouting code imports only the standard library and psycopg; the `scout` container gets no DeepSeek key (compose passes only `db-roles.env`). |
| 2 | **Idempotent:** a second sweep adds no rows or events | **Pass** | Sweep run 2: `new 0, changed 0, seen again 177`; enrichment run 7: `new 0, seen again 4`; duplicate groups `0 changed`. Tests: `test_rerun_with_same_data_adds_nothing`, `test_rerun_and_sweep_keep_everything_stable`. |
| 3 | **Isolation:** a failing term or board doesn't affect the others | **Pass (tests)** | Every live term and board succeeded, so this rests on tests: `test_persistent_failure_is_transient_and_others_continue`, `test_failing_board_does_not_stop_the_others`. |
| 4 | **Removal safety:** a failed sweep marks nothing missed | **Pass** | Tests: `test_failed_term_marks_nothing_missed`, `test_term_subset_marks_nothing_and_keeps_terms`, `test_all_empty_sweep_is_structural_and_marks_nothing`. Live: 2 postings that closed on WorkBC (177 → 175) were counted missed in runs 4, 6 and 8 and removed, not deleted. Since those runs were 5 minutes apart, task 1.10 added a second condition: 24 hours unseen. |
| 5 | **Trust:** seeded scams flagged with reasons and kept (plan acceptance test 12) | **Pass** | Tests: `test_red_flags`, `test_scam_is_flagged_and_kept_in_the_database`. Live: 0 suspicious; scores +3: 41, +2: 132, 0: 8. |
| 6 | **Reader role:** `scout_reader` reads everything, changes nothing (plan acceptance test 15) | **Pass** | `scout db check`: 49/49 after every migration (0001–0004). |
| 7 | **Live run:** real sweep loads ~170+ postings; areas, ATS types and duplicate groups look right | **Pass** | 177 then 175 postings; checked by hand: area and remote reasons, possible matches, 9 duplicate groups, 25 job boards. |

**Phase 1: done** (2026-10-31).

## What Scout knows now (live, 2026-10-31)

- **WorkBC sweep:** 10 title-only terms across B.C.; about 12 requests in 16 s; 175–177 unique
  postings (199 term hits; 22 found by more than one term); 4 Job Bank.
- **Area:** 165–167 Metro Vancouver, 6 remote, 4 Victoria.
- **Title match** (Metro Vancouver and remote): 146–148 strong, 25 possible.
- **Employers:** 95 after merging name variants.
- **Job-board systems:** own career sites 63, Workday 43–45, Greenhouse 23, Ashby 12, Lever 11,
  Oracle 6, Workable 4, Job Bank 4, others 9.
- **Duplicate groups:** 9 groups, 19 postings. Activision ×3 (one role, three names and sites);
  same-title reposts (Impact.com, Konrad, EA, Amazon ×3); same role in two cities (Aritzia,
  Workday).
- **Job-board details:**
  - All 25 Ashby, Greenhouse and Lever boards answered.
  - 37 WorkBC postings matched: descriptions 37, pay 10, workplace 21.
  - 4 Greenhouse boards found from `?gh_jid=` links (Asana, Branch Metrics, Brex, Databricks).
  - 4 jobs found only on boards: AI Engineer, Vancouver; Data Scientist, remote in Ontario;
    two remote AI engineering roles.
- **Pay seen:** CA$90k–205k a year for the matched data and AI roles; one hourly (CA$35–38).
- **Trust:** no suspicious postings.

## What the live runs changed

| Found on the box | Fix |
|---|---|
| An apt-installed Postgres held port 5432 | Disabled it; troubleshooting entry in box-setup |
| The `count`/`result` keys are lowercase | Client accepts both cases |
| WorkBC's remote filter only works for Job Bank postings | Area classified in Scout's own code |
| One posting listed twice under two JobIds would fail the save | Migration 0002 |
| Board-only jobs: "AI" alone matched "artificial intelligence"; "Remote – Sweden" passed | Literal term words, remote only in Canada (PR #9) |
| Three quick sweeps removed closed jobs in 5 minutes | Removal also needs 24 hours unseen (task 1.10) |

## Carried into later phases

- **Phase 2:** run sweeps on a schedule (the plan suggests up to twice a day), the daily email
  digest, database backups and the WSL2 sync.
- **Phase 3, the agent's first tasks:**
  - Workday details (43–45 postings, the largest group).
  - The large own career sites (Amazon, EA, lululemon).
  - Connor, Clark & Lunn's Greenhouse board, which Scout couldn't find from the name.
- **Open questions:**
  - Any title with "AI" or "ML" counts as a strong match (146 of 171). Revisit after a few weeks
    of data.
  - Greenhouse pay ranges need one extra request per job.
  - Lookalike domains aren't part of the trust score yet.
