# Phase 2 plan: runs on its own, daily digest, backups, sync

Status: **in progress** (2026-10-31). 2.1 and 2.2 done on the box; 2.3 built. No email: the
digest is a file on the box that you read yourself (§5).
Architecture plan: [architecture-plan.md](architecture-plan.md) §7, §10, §11, §13.
Still zero LLM tokens: nothing in Phase 2 involves the agent.

**Goal:** Scout runs every day without you. It sweeps WorkBC twice a day, writes a short digest
every morning, backs up its database every night, and your WSL2 machine pulls copies of the
backups and digests. **Exit:** one week unattended, with a digest every day.

## 1. What runs, and when

| What | When (Vancouver time) | How |
|---|---|---|
| Sweep + job-board enrichment | 06:40 and 16:40 | `scout sweep` in the `scout` container |
| Digest file | 07:10 (after the morning sweep) | `scout digest` in a separate `digest` container |
| Database backup | 03:30 | `ops/backup.sh` on the host: `pg_dump` from the `db` container |
| Old raw files pruned | after each sweep | raw WorkBC pages older than 30 days |
| Old digests pruned | after each digest | digest files older than 90 days |
| Pull to WSL2 | every 6 hours and at Windows login | Windows Task Scheduler runs a WSL2 script |

The minutes are spread out so jobs don't overlap, and none land at :00. Twice a day is about 24
WorkBC requests and 50 job-board requests a day.

## 2. Decisions (mine; say if you want any changed)

1. **Timers on the host, not an always-on scheduler container.** The architecture plan drew a
   "runtime container, always on" with its own scheduler. systemd timers on the box do the same
   job with less to break:
   - Each run starts a fresh container and exits, like when you run it by hand.
   - A run missed while the box was off happens at the next boot (`Persistent=true`).
   - Output goes to the system journal (`journalctl -u scout-sweep`).
   - Phase 3 will start agent sessions the same way, so there's one mechanism for both.

   The unit files live in the repo (`ops/systemd/`); one install script copies them into
   `/etc/systemd/system` and runs them as `shaw2012`.
2. **No overlapping runs.** A sweep or enrichment takes a database lock (a Postgres advisory
   lock, released automatically if the run crashes). A second one, from a timer or by hand,
   prints that it was skipped and exits. systemd never starts a timer's job twice at once.
3. **The digest service.** A separate `digest` service uses the same image but:
   - reads the database as `scout_reader`, and records each digest as `scout_ingest` (the only
     write it makes);
   - can write only to `/srv/scout/logs/digests`;
   - reads `/srv/scout/logs` read-only for the backup status and the sync time.
4. **Backups:**
   - A compressed dump every night into `/srv/scout/backups` (owned by you, mode 750; the
     containers can't write there).
   - Kept: the last 14 daily dumps and the last 8 Sunday dumps. The database is small (a few MB).
   - Each backup is checked by restoring it into a scratch database and comparing row counts;
     the scratch database is then dropped. A dump that doesn't restore counts as a failed backup.
5. **WSL2 sync key:** a separate SSH key, allowed only to read: the backups and the digests. It
   can't open a shell or write files. Each use leaves a timestamp in
   `/srv/scout/logs/sync/last-sync`, so the digest can say "sync to WSL2: N hours ago". The
   architecture plan also syncs the agent's repo; that repo arrives in Phase 3 and will be added
   to the same key then.
6. **No alerts.** Without email there's no channel to push an alert through, so problems go at
   the top of the digest (and in its title line). If today's digest is missing or old, that is
   itself the sign something is wrong: check the date on its first line. A phone notification
   (for example ntfy, counts only, no job details) can be added later if you want one.
7. **"New" in the digest means new since the last digest written**, not "last 7 days". The
   `digests` table records each one (migration 0005). If a digest fails, the next one still
   covers everything since the last successful one.

## 3. The digest

Two files a day in `/srv/scout/logs/digests/`, named by Vancouver time: `2026-11-01-0710.txt`
(plain text) and `2026-11-01-0710.html` (the same, with clickable links). `latest.txt` and
`latest.html` always hold the newest. WorkBC data stays on the box (and your WSL2 copy).
Suspicious postings appear only in their own section, never among the new jobs. The digest
reads the backup status from `/srv/scout/logs/backup-status` (a copy `ops/backup.sh` writes,
since the container can't see into the backups folder).

```
Scout: 6 new jobs, 0 suspicious (Sun Nov 1)

Scout digest for Sun Nov 1: since Sat Oct 31 07:10.
Relevant jobs open right now: 150.

NEW RELEVANT JOBS (6)    Metro Vancouver or remote, strong title match
  AI Engineer — Remarcable, Inc. — Vancouver (hybrid) — CAD 120,000–150,000
    https://jobs.ashbyhq.com/remarcable-inc/…
  …

NEW POSSIBLE MATCHES (2)    looser title match: worth a glance
CLOSED (3)    gone from WorkBC or the job board (missed 3 sweeps over 24 hours)
SUSPICIOUS (0)    red flags outweigh good signs; kept, not hidden (v_suspicious)

HEALTH
  sweeps: 2 of 2 ok since the last digest; last good sweep Sat 16:41
  sources: all ok (25 job boards and WorkBC)
  backup: 4 hours ago, 0.2 MB, restore check ok
  sync to WSL2: 5 hours ago
  disk: 83 GB free
```

- Each job links to the employer's own posting when Scout has it, otherwise to the WorkBC job
  page (`…/search-jobs#/job-details/<JobId>`).
- Long lists are cut at 25 with "… and N more (`scout status`)".
- Problems, listed at the top under NEEDS YOUR ATTENTION:
  - no good sweep in 24 hours, or a failed sweep;
  - a degraded source;
  - the backup failed, is missing, or is over 36 hours old;
  - the sync is over 48 hours old;
  - less than 5 GB of disk.
- Sections the agent will fill (what it worked on, commits to promote) come in Phase 3.
- `scout digest --dry-run` prints it without writing or recording anything.

## 4. Build order

| Task | What | Done when |
|---|---|---|
| 2.1 | **Timers:** `ops/systemd/` units for sweep and backup; `ops/systemd/install.sh`; the run lock; raw-file pruning | `systemctl list-timers 'scout-*'` shows the next runs; a sweep runs by timer and shows in `scout status`. **Done** (2026-10-31) |
| 2.2 | **Backups:** `ops/backup.sh` (dump, restore check, keep 14 daily + 8 weekly) | a dump in `/srv/scout/backups`; the restore check passes; a broken dump is reported as failed. **Done** (2026-10-31: 181 postings restored) |
| 2.3 | **Digest:** migration 0005 (`digests`); `scout digest [--dry-run]`; `digest` service; `scout-digest.timer` | a digest file on the box with the real jobs; tests cover "since last digest", empty days and a failed write. **Built** |
| 2.4 | ~~Email~~ | **Dropped** (2026-10-31): Gmail app passwords aren't available for your account; you read the digest yourself |
| 2.5 | ~~Health alerts~~ | **Dropped** with email: problems go at the top of the digest instead |
| 2.6 | **WSL2 sync:** the restricted read-only key on the box (`ops/sync/`), the pull script for WSL2 (backups and digests), Task Scheduler steps | backups and digests appear in WSL2; the key can't open a shell; the digest shows the sync time |
| 2.7 | **A week unattended** and `docs/phase2-results.md` | 7 digests in a row; no missed sweeps; backups and sync current |

Each task is one PR, as in Phase 1 (2.1 and 2.2 shared one).

## 5. No email (decided 2026-10-31)

The first design emailed the digest through a Gmail account with an app password. Google
didn't offer app passwords for your account ("The setting you are looking for is not available
for your account"), so the digest is written to files instead and you check it yourself:

```bash
ssh scoutbox cat /srv/scout/logs/digests/latest.txt      # from WSL2
```

Nothing leaves the box, and there are no passwords or accounts to keep. Once the WSL2 sync
(2.6) runs, the digest files are on your PC too, and `latest.html` opens in a Windows browser
with clickable links.

## 5a. Box facts (checked 2026-10-31)

- Time zone `America/Vancouver`, clock synchronized by NTP. B.C. stays on UTC-7 from
  2026-11-01 (permanent daylight time); the box's time zone data shows it as "MST".
- Ubuntu server (no desktop), up for days at a time; nothing puts it to sleep.
- Docker starts at boot; `rsync` and `rrsync` are installed; 83 GB free.
- Inside the db container, local connections need no password, so `pg_dump` runs there as
  `postgres`. The port published on the box (127.0.0.1:5432) still needs a password.

## 6. Exit criteria

1. **Unattended week:** 14 sweeps (at most one missed and caught up), 7 digests, 7 backups, with
   no commands from you.
2. **Zero tokens:** no LLM calls (acceptance test 1).
3. **Independence:** scouting runs with the agent container stopped (acceptance test 5).
4. **Sync key:** it can read the backups and digests, and can't open a shell or write
   (acceptance test 13).
5. **Restore:** last night's dump restores into a scratch database with the same row counts.

## 7. Not in Phase 2

- Anything the agent does (Phase 3), including the agent's repo in the WSL2 sync.
- Push notifications (ntfy or similar). Possible later, counts only.
- Reading the database from Windows over an SSH tunnel (architecture plan §8). It already works
  if you want it; it's a how-to, not a build task.
- A full restore onto a new machine (Phase 5).
