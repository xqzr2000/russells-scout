# Phase 2 results

Plan: [phase2-plan.md](phase2-plan.md). Scout ran unattended on the Ubuntu box from 2026-11-01
to 11-07: system timers, no agent and no commands from you. Numbers are from the command output
pasted back on 2026-11-07 at about 20:20, after the 14th sweep.

## Exit criteria (phase2-plan.md §6)

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | **Unattended week:** 14 sweeps (at most one missed and caught up), 7 digests, 7 backups, no commands from you | **Pass** | **Sweeps 14/14:** runs #16–#42, at 06:40–06:42 and 16:40–16:42 each day from 11-01 to 11-07. All `ok`, 10 of 10 terms each time, none missed, none caught up. **Digests 7/7:** `2026-11-01-0710` to `2026-11-07-0711`. **Backups 7/7:** one dump each night at 03:30–03:31. The journal has no warnings from `scout-sweep`, `scout-backup` or `scout-digest` since 11-01. |
| 2 | **Zero tokens:** no LLM calls (acceptance test 1) | **Pass** | `docker ps -a --filter name=agent` lists no container: the agent never ran. The `scout` and `digest` containers get no DeepSeek key (Phase 1, criterion 1). |
| 3 | **Independence:** scouting runs with the agent container stopped (acceptance test 5) | **Pass** | The whole week ran with no agent container on the box (see 2). |
| 4 | **Sync key:** reads backups and digests; can't open a shell or write (acceptance test 13) | **Pass** | Refusals checked on 2026-10-31: no shell, no commands, `secrets/` refused. During the week every logged pull was `backups: ok`, `digests: ok`. Task Scheduler: last result 0, 0 missed runs. The box stamped the last pull at 2026-11-07 19:00. |
| 5 | **Restore:** last night's dump restores into a scratch database with the same row counts | **Pass** | `backups/status` for 2026-11-07 03:30: `result=ok`, `scout-20261107-033027.dump`, 194,807 bytes, `restore check ok`. |

**Phase 2: done** (2026-11-07).

## What the week looked like

- **Sweeps:** each sweep is followed by a job-board run. The last three read every board (24–25
  boards, 0 failed). "Sources needing attention: none."
- **Postings** (11-07): 186 active (182 from WorkBC, 4 only on job boards), 23 removed, 22 new
  in the last 7 days. On 10-31 there were 175 active.
  - Area: Metro Vancouver 175, remote 6, other B.C. 5.
  - Strong matches (Metro Vancouver or remote): 153. Possible matches to review: 28.
  - Job-board details: 39 postings, 13 of them with pay.
  - Duplicates: 14 groups, 28 postings (9 groups on 10-31).
  - Trust: +3: 39, +2: 139, 0: 8. Nothing below 0.
- **Digest** (11-07): 3 new jobs, 0 problems. The timers start up to 1–2 minutes late on purpose
  (`RandomizedDelaySec`), so some files are stamped 07:11 and some backups 03:31.
- **Backups:** dumps grew from 179 KB (11-01) to 195 KB (11-07). There are 10 so far: 3 from
  setup on 10-31 plus 7 nightly. Retention (14 daily + 8 Sundays) hasn't removed any yet.
- **Sync:** pulls run only while you're logged in to Windows. When the PC was off there were
  gaps (for example, from 11-04 20:21 to 11-06 13:55), and each gap ended with a pull at logon.
  The 07:00 Windows pull runs before the 07:10 digest, so the morning digest reaches the PC at
  13:00, or earlier with a manual pull.

## After the week: reboot

On 2026-11-07, after the check, Ubuntu updates were installed (29 packages, kernel 7.0.0-42 →
7.0.0-43) and the box was rebooted. Scout came back on its own:
- the `db` container was running, since `psql` through `docker compose exec` worked;
- all three timers were listed with their next runs, at 03:31, 06:41 and 07:10 on 11-08.

## Found during the week

| Found | Fix |
|---|---|
| Your ssh alias for the box is `ubuntu-box`, not `scoutbox` | box-setup now says the guide's name may differ from yours |
| Pasting all of §18.5 into PowerShell at once ran the lines out of order (Start, then Register with an empty `$action`) | §18.5 now says to paste one line at a time |
| Reading `db-roles.env` doesn't need `sudo` (`secrets/` belongs to `shaw2012`), and the database tables are in schema `scout` | The DBeaver steps in §12 now say so |

## Carried into Phase 3

- **The agent** (architecture plan §17, in this order):
  1. Its routine: when sessions run, what each does, turn limits.
  2. How agent changes go live: tests, canary, rollback.
  3. First tasks: Workday details (43–45 postings), then the big career sites (Amazon, EA,
     lululemon).
- **Open questions:**
  - Any title with "AI" or "ML" counts as a strong match (153 of 186 active). Revisit with more
    weeks of data.
  - WorkBC lists some postings as Vancouver whose links go to jobs in other cities: Accenture,
    Microsoft and BDO (Toronto), NetApp (Morrisville). Consider using the link's or job board's
    location.
  - EA "AI Engineer Intern" 216223 appears twice with different titles and isn't grouped.
  - Greenhouse pay ranges need one extra request per job.
  - Connor, Clark & Lunn's Greenhouse board isn't known; its links use `cclfg`/`cclim.cclgroup.com`
    `?gh_jid=`.
  - Lookalike domains aren't part of the trust score yet.
  - 8 postings have trust 0. Binance.US is one: +2 for its job board, −2 because its pay is far
    above normal.
- **Box upkeep (optional):** Ubuntu Pro / ESM Apps offers 16 more security updates. It's free
  for personal use, but needs an Ubuntu One account. Not needed for Scout, whose services run in
  Docker images.
