# Phase 2 plan: runs on its own, daily digest, backups, sync

Status: **in progress** (2026-10-31). 2.1 and 2.2 built. Email: a Gmail account (§5).
Architecture plan: [architecture-plan.md](architecture-plan.md) §7, §10, §11, §13.
Still zero LLM tokens: nothing in Phase 2 involves the agent.

**Goal:** Scout runs every day without you. It sweeps WorkBC twice a day, emails you a short
digest every morning, backs up its database every night, and your WSL2 machine pulls copies of
those backups. **Exit:** one week unattended, with a digest every day.

## 1. What runs, and when

| What | When (Vancouver time) | How |
|---|---|---|
| Sweep + job-board enrichment | 06:40 and 16:40 | `scout sweep` in the `scout` container |
| Digest email | 07:10 (after the morning sweep) | `scout digest` in a separate `digest` container |
| Database backup | 03:30 | `ops/backup.sh` on the host: `pg_dump` from the `db` container |
| Health check | every hour at :15 | `scout health`; emails only when something is broken |
| Old raw files pruned | after each sweep | raw WorkBC pages older than 30 days |
| Backup pull to WSL2 | every 6 hours and at Windows login | Windows Task Scheduler runs a WSL2 script |

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
3. **Email credentials only in the digest container.** A new `digest` service uses the same image
   but:
   - logs in to the database as `scout_reader`;
   - is the only service given `/srv/scout/secrets/mail.env`;
   - takes the recipient from `mail.env`, never from config or the database.

   The sweep container never sees the email password, and the agent container (Phase 3) never
   sees it either.
4. **Python's own `smtplib`.** No new package. Port 587 with STARTTLS.
5. **Backups:**
   - A compressed dump every night into `/srv/scout/backups` (owned by you, mode 750; the
     containers can't write there).
   - Kept: the last 14 daily dumps and the last 8 Sunday dumps. The database is small (a few MB).
   - Each backup is checked by restoring it into a scratch database and comparing the posting
     count; the scratch database is then dropped. A dump that doesn't restore counts as a failed
     backup.
6. **WSL2 sync key:** a separate SSH key, allowed to do one thing: read `/srv/scout/backups`
   (`rrsync -ro`). It can't open a shell or write files. Each use leaves a timestamp in
   `/srv/scout/logs/sync/last-sync`, so the digest can say "last sync: N hours ago". The
   architecture plan also syncs the agent's repo; that repo arrives in Phase 3 and will be added
   to the same key then.
7. **Alerts right away** only for these (architecture plan §10):
   - no successful sweep for 24 hours;
   - the database is down.

   (If email itself is broken, no alert can arrive either; a missing morning digest is the sign.)

   At most one alert email every 12 hours for the same problem, so a dead box doesn't flood your
   inbox.
8. **"New" in the digest means new since the last digest that was sent**, not "last 7 days". A
   new `digests` table records each one sent (migration 0005). If a digest fails, the next one
   still covers everything since the last successful one.

## 3. The digest

One email a day, plain text with a simple HTML version. WorkBC data stays private: it goes only
to your own address.

```
Subject: Scout: 6 new jobs, 0 suspicious (Sat Oct 31)

NEW RELEVANT JOBS (6)               Metro Vancouver or remote, strong title match
  AI Engineer — Remarcable, Inc. — Vancouver (hybrid) — CAD 120k–150k
    https://jobs.ashbyhq.com/remarcable-inc/…
  Machine Learning Engineer — Clio — Vancouver
    https://…
  …

POSSIBLE MATCHES TO REVIEW (2)       looser title match
  Senior Data Quality Analyst — BDO — Vancouver

CLOSED SINCE LAST DIGEST (3)         gone from WorkBC for 3 sweeps and 24 hours
  Data Analyst — Konrad

SUSPICIOUS (0)

HEALTH
  sweeps: 2 of 2 ok since the last digest; WorkBC ok; 25 job boards ok
  backup: last night, 1.2 MB, restore check ok
  sync to WSL2: 5 hours ago
  disk: 79 GB free
```

- Each job links to the employer's own posting when Scout has it, otherwise to WorkBC.
- Long lists are cut at 25 with "… and N more (`scout status`)".
- Sections the agent will fill (what it worked on, commits to promote) come in Phase 3.
- `scout digest --dry-run` prints the email instead of sending it, without recording it.

## 4. Build order

| Task | What | Done when |
|---|---|---|
| 2.1 | **Timers:** `ops/systemd/` units for sweep and backup (digest and health join in 2.3 and 2.5); `ops/systemd/install.sh`; the run lock; raw-file pruning | `systemctl list-timers 'scout-*'` shows the next runs; a sweep runs by timer and shows in `scout status`. **Built** |
| 2.2 | **Backups:** `ops/backup.sh` (dump, restore check, keep 14 daily + 8 weekly) | a dump in `/srv/scout/backups`; the restore check passes; a broken dump is reported as failed. **Built** |
| 2.3 | **Digest:** migration 0005 (`digests`); `scout digest [--dry-run]`; `digest` service in compose | `--dry-run` on the box shows the real jobs; tests cover "since last digest", empty days and a failed send |
| 2.4 | **Email:** `mail.env`, `smtplib` sender, `scout digest --test-email` | a test email arrives in your inbox |
| 2.5 | **Health alerts:** `scout health` (last good sweep, DB reachable), at most every 12 hours | stopping the db container produces one alert email, and no second one within 12 hours |
| 2.6 | **WSL2 sync:** the restricted key on the box (`ops/sync/`), the pull script for WSL2, Task Scheduler steps | backups appear in WSL2; the key can't open a shell; the digest shows the sync time |
| 2.7 | **A week unattended** and `docs/phase2-results.md` | 7 digests in a row; no missed sweeps; backups and sync current |

Each task is one PR, as in Phase 1 (2.1 and 2.2 share one).

## 5. Email account: Gmail (decided 2026-10-31)

The digest is sent through a Gmail account (`smtp.gmail.com`, port 587, STARTTLS) with an
**app password**: a separate 16-character password made for Scout, which you can revoke at any
time without changing the account's real password. Gmail requires 2-Step Verification before it
offers app passwords. The password goes in `/srv/scout/secrets/mail.env` (mode 600, never in
git). Step-by-step instructions come with task 2.4.

An app password can read the mailbox as well as send. If the Gmail account also holds your
everyday mail, a new Gmail account made just for Scout keeps that password away from it; either
works. (The other option considered was your iCloud address.)

## 5a. Box facts (checked 2026-10-31)

- Time zone `America/Vancouver`, clock synchronized by NTP.
- Ubuntu server (no desktop), up for days at a time; nothing puts it to sleep.
- Docker starts at boot; `rsync` and `rrsync` are installed; 83 GB free.
- Inside the db container, local connections need no password, so `pg_dump` runs there as
  `postgres`. The port published on the box (127.0.0.1:5432) still needs a password.

## 6. Exit criteria

1. **Unattended week:** 14 sweeps (at most one missed and caught up), 7 digests, 7 backups, with
   no commands from you.
2. **Zero tokens:** no LLM calls (acceptance test 1).
3. **Independence:** scouting runs with the agent container stopped (acceptance test 5).
4. **Secrets:** only the `digest` service gets `mail.env`; the sync key opens no shell
   (acceptance tests 9 and 13, the parts that exist in Phase 2).
5. **Alerts:** a stopped database produces one alert, not a flood.
6. **Restore:** last night's dump restores into a scratch database with the same posting count.

## 7. Not in Phase 2

- Anything the agent does (Phase 3), including the agent's repo in the WSL2 sync.
- Reading the database from Windows over an SSH tunnel (architecture plan §8). It already works
  if you want it; it's a how-to, not a build task.
- A full restore onto a new machine (Phase 5).
