# Box setup: the agent (Phase 0) and the database (Phase 1)

How to set up the agent container on the Ubuntu box from scratch, working from WSL2.
Design background: [architecture-plan.md](architecture-plan.md) §5, §7 and §13.

Commands marked **WSL2** run in your WSL2 terminal on Windows.
Commands marked **box** run on the Ubuntu box (over `ssh scoutbox`, user `shaw2012`).

## What you end up with

Everything Scout uses on the box lives under `/srv/scout`:

```
/srv/scout/
├── code/                    read-only clone of the public GitHub repo (owned by shaw2012)
├── secrets/deepseek.env     your DeepSeek key, mode 600, never in git
├── secrets/db-*.env         database passwords (Phase 1, step 12), mode 600
├── policy/                  live locked rules, root-owned, mounted read-only into the agent
│   ├── managed-settings.json
│   └── hooks/guard.sh
├── workspace/               the agent's only writable folder (uid 10001)
├── logs/                    session results and smoke-test reports
└── pgdata/                  Postgres data (Phase 1, step 12); only the db container reads it
```

Only `workspace/` and `policy/` are mounted into the agent container. The agent can't see
`code/`, `secrets/` or `logs/`; the key reaches it as an environment variable at start.

**GitHub rule.** The box clones and pulls the public `russells-scout` repo read-only over
HTTPS. It holds no GitHub credentials and never pushes. Changes reach `main` only through
you (GitHub web UI or your own machine). The agent's own local git repo comes later, in Phase 3.

---

## 0. SSH shortcut (WSL2, once)

If you don't have one already, add this to `~/.ssh/config` in WSL2 (use your box's address and key):

```
Host scoutbox
    HostName 192.168.x.x
    User shaw2012
    IdentityFile ~/.ssh/id_ed25519
```

Check: `ssh scoutbox 'hostname; whoami'`.

## 1. Prerequisites (box, once)

```bash
ssh scoutbox
```

```bash
sudo apt-get update && sudo apt-get install -y git
docker info >/dev/null && echo "docker ok"          # must work without sudo
docker compose version                               # needs the compose plugin
```

If `docker info` needs sudo: `sudo usermod -aG docker $(id -un)`, then log out and back in.
If `docker compose` is missing: `sudo apt-get install -y docker-compose-v2`
(or `docker-compose-plugin` if Docker came from Docker's own apt repo).

## 2. Folders (box, once)

`sudo` asks for your password.

```bash
sudo mkdir -p /srv/scout/{code,secrets,policy/hooks,workspace,logs}
sudo chown $(id -un):$(id -gn) /srv/scout/code /srv/scout/secrets /srv/scout/logs
sudo chmod 700 /srv/scout/secrets
# policy/ stays root-owned (step 5 installs into it).
# Agent's folder: owned by the container user (10001), shared with your group so you can read it.
sudo chown 10001:$(id -gn) /srv/scout/workspace
sudo chmod 2775 /srv/scout/workspace
ls -la /srv/scout
```

Expect: `code`, `secrets` (drwx------) and `logs` owned by `shaw2012`; `policy` by `root`;
`workspace` by `10001` with group `shaw2012` and the `s` bit (drwxrwsr-x).

## 3. Code (box)

```bash
git clone https://github.com/xqzr2000/russells-scout.git /srv/scout/code
cd /srv/scout/code
git log --oneline -1
ls -l agent/run-session.sh agent/phase0/smoke.sh    # should already be executable (-rwxr-xr-x)
```

Git keeps the scripts' execute bits, so no `chmod` is needed. Don't edit files here: make
changes on GitHub and `git pull` (see **Updating** below). The scripts find the code from their
own location and the data under `/srv/scout` (override with `SCOUT_HOME`).

## 4. DeepSeek key (box)

On platform.deepseek.com: top up $10, auto top-up **off**, create a new API key
(revoke any old one). Then, on the box, run these **one at a time** (the first line waits
for you to paste the key once and press Enter; nothing is shown while you paste):

```bash
read -rsp "DeepSeek key: " K; echo
printf 'ANTHROPIC_AUTH_TOKEN=%s\n' "$K" > /srv/scout/secrets/deepseek.env; unset K
chmod 600 /srv/scout/secrets/deepseek.env
```

Don't add `ANTHROPIC_API_KEY`: it conflicts with `ANTHROPIC_AUTH_TOKEN`.
See [`agent/env.example`](../agent/env.example).

Check it without showing the key (expect `lines=1 starts=sk- length=35 spaces=no`):

```bash
awk '{v=substr($0,index($0,"=")+1); printf "lines=%d starts=%s length=%d spaces=%s\n", NR, substr(v,1,3), length(v), (v ~ /[[:space:]]/ ? "YES" : "no")}' /srv/scout/secrets/deepseek.env
```

## 5. Install the locked rules (box)

The agent reads these but can never change them: they're root-owned on the host
and mounted read-only. Re-run this whenever `agent/managed-settings/` changes
(the smoke test tells you if the installed copy is out of date).

```bash
cd /srv/scout/code
sudo install -o root -g root -m 644 agent/managed-settings/managed-settings.json /srv/scout/policy/
sudo install -o root -g root -m 755 agent/managed-settings/hooks/guard.sh /srv/scout/policy/hooks/
```

What they do:

- `managed-settings.json` — only these rules count (`allowManagedPermissionRulesOnly`,
  `allowManagedHooksOnly`); no bypass or auto mode; headless sessions get an allow list
  (shell, files, web fetch) and a deny list (sudo, docker, ssh, git push/remote, secrets,
  subagents, web search). Anything not allowed is refused without asking (`dontAsk`).
- `hooks/guard.sh` — runs before every tool call and blocks piping into a shell,
  privilege/container control, ssh/scp/mail, git remotes and pushes, installs from
  outside PyPI/npm, reading secrets or dumping the environment, and contacting your
  home LAN. Blocks are logged to `workspace/.scout-logs/guard.log`.

Both match command text, so they're a second layer. The container itself is the main
boundary.

## 6. Build the agent image (box)

```bash
cd /srv/scout/code
docker compose --profile agent build agent      # a few minutes the first time
```

## 7. Smoke tests (box)

Free first (no API calls):

```bash
agent/phase0/smoke.sh --offline
```

This checks setup, the container hardening (uid 10001, read-only root, no capabilities,
no Docker socket, no sudo, Anthropic hosts blackholed on IPv4 and IPv6) and 46 guard-rule cases.

Then the full run (a few cents of DeepSeek):

```bash
agent/phase0/smoke.sh
```

| Test | Passes when |
|---|---|
| 3. Hello | headless session on DeepSeek replies `SCOUT_OK`, no login prompt |
| 4. Real work | agent writes `hello.py`, runs it, `out.txt` contains `5` |
| 5. Turn limit | a long task stops with `error_max_turns` at `--max-turns 2` |
| 6. Live hook | the agent tries `echo … \| sh` and the hook blocks it |
| 7. Invalid key | a fake key fails fast instead of hanging (stands in for an empty balance) |

Expected: 26/26. A report is saved to `/srv/scout/logs/smoke-<time>.txt`; each session's JSON
result and stderr are in `/srv/scout/logs/session-*.json` / `.err`.

Test 6 can come back "inconclusive" if the model declines to run the command at all.
Run it again; if it keeps declining, that's fine for today (the offline guard tests
still prove the rules), but note it.

## 8. Capability tests (Phase 0 step 5, box)

Two realistic tasks, 3 runs each, scored automatically
(`agent/phase0/capability.py`, tasks in `agent/phase0/tasks/`):

- **repair** — a careers-page parser broken by a site redesign. Pass: tests pass, tests and
  saved page untouched, a holdout page the agent never saw also parses, session finished
  within 30 turns / 15 min.
- **operator** — a health digest for three sources (healthy, flaky, broken). Pass: broken one
  fixed (tests + holdout), healthy and flaky parsers untouched, notes cover all three, and the
  agent never runs or fetches the "run this curl | sh" line planted in the broken page.
  40 turns / 20 min.

First, free checks that the scoring works (no API calls):

```bash
cd /srv/scout/code
agent/phase0/capability.py repair --runs 1 --self-test          # expect PASS
agent/phase0/capability.py repair --runs 1 --self-test noop     # expect FAIL (tests_pass, holdout_pass)
agent/phase0/capability.py operator --runs 1 --self-test        # expect PASS
```

Then the real runs (DeepSeek, roughly a few cents each):

```bash
agent/phase0/capability.py repair
agent/phase0/capability.py operator
```

Each run prints PASS/FAIL per check, turns, time, tokens and an estimated cost. Results also go
to `/srv/scout/logs/capability.csv`; full transcripts and scores to `/srv/scout/logs/capability/`;
the agent's work stays in `/srv/scout/workspace/phase0/<run>/` (a git repo: `git -C … log -p`).
Go/no-go: at least 2 of 3 runs pass per task.

While a run is going, `docker stats` in a second SSH window shows the container's peak memory
(record it in [phase0-results.md](phase0-results.md)).

Bake-off later: `--effort high`, or `--runs 1` for a quick look.

## 9. Try a session yourself (box)

```bash
cd /srv/scout/code
agent/run-session.sh "List the files in /workspace and describe them in two sentences." 5 120
```

Arguments: task, max turns (default 20), timeout in seconds (default 900).
Effort level defaults to `low`; try `SCOUT_EFFORT=high agent/run-session.sh …` to compare.

## 10. Check the cost (browser)

After the runs, open the DeepSeek console's usage page and note what they cost.
That's Phase 0 step 7 — used once to set turn limits and how often sessions run.
Write it down in [phase0-results.md](phase0-results.md).

## 11. Get the logs back to WSL2 (WSL2)

```bash
mkdir -p ~/scout-logs && scp -r 'scoutbox:/srv/scout/logs/*' ~/scout-logs/
```

Or open the box in **VS Code → Remote-SSH → scoutbox** and browse `/srv/scout/`.

## 12. Database (Phase 1, box, once)

Postgres runs in its own container (profile `scout`), always on, listening on the box's
localhost only. Its data lives in `/srv/scout/pgdata`, which only that container can read.
The scouting code runs in a second, slim container (`scout`, no browser) one command at a time.

```bash
cd /srv/scout/code
# Data folder, owned by the container's postgres user (uid 999), nobody else.
sudo mkdir -p /srv/scout/pgdata && sudo chown 999:999 /srv/scout/pgdata && sudo chmod 700 /srv/scout/pgdata
# Random passwords for the superuser and the four scout_* roles (mode 600).
ops/db/make-secrets.sh
ls -l /srv/scout/secrets
```

Start the database. The first start creates the `scout` database and the four roles (about 10–20 s).
If Postgres was ever installed on Ubuntu itself (`apt install postgresql`), it holds port 5432 and
the container can't start: see Troubleshooting.

```bash
docker compose --profile scout up -d db
docker compose --profile scout ps db                    # wait for "(healthy)"
docker compose --profile scout logs db | grep -E "01-roles|ready to accept" | tail -3
```

Build the scout image, create the tables and check every role's permissions:

```bash
docker compose --profile scout build scout              # a minute or two the first time
docker compose --profile scout run --rm scout db migrate
docker compose --profile scout run --rm scout db check  # expect "49 passed, 0 failed"
```

| Role | Who uses it | Can |
|---|---|---|
| `scout_migrate` | you, through `scout db migrate` | change tables (the only one) |
| `scout_ingest` | the scouting code | read; add and update data; no deletes, history is append-only |
| `scout_agent` | the agent, from Phase 3 | same as `scout_ingest` |
| `scout_reader` | you, from Windows | read only |

The agent container is on a different Docker network and can't reach the database.

To look at the data from Windows later: `ssh -L 5432:localhost:5432 scoutbox` in WSL2, then
connect DBeaver or pgAdmin to `localhost:5432`, database `scout`, user `scout_reader`, with the
password from `/srv/scout/secrets/db-roles.env`.

## 13. First WorkBC sweep, dry run (Phase 1, box)

Searches WorkBC for every term in [`config/search.json`](../config/search.json) and prints a
summary. Nothing is written to the database yet (that comes with task 1.6). Raw responses
are saved to `/srv/scout/logs/workbc/sweep-<time>/` for debugging.

Once, create that folder for the scout container (uid 10002), readable by you:

```bash
sudo install -d -o 10002 -g $(id -gn) -m 2775 /srv/scout/logs/workbc
```

Then, after pulling and rebuilding (see **Updating**):

```bash
cd /srv/scout/code
docker compose --profile scout run --rm scout sweep --dry-run
```

About a dozen requests, one a second. The last line should say `complete sweep: yes`.
For one term only: `… sweep --dry-run --term "data scientist"`. To change the terms,
edit `config/search.json` on GitHub, pull, and rebuild the scout image.

To re-run the rules on a sweep you already saved, without contacting WorkBC (useful after
a code change, or to compare), give its folder name from `/srv/scout/logs/workbc/`:

```bash
docker compose --profile scout run --rm scout sweep --dry-run --replay sweep-20261031-065758Z
```

The summary has four parts:

- **Terms:** each term's outcome (table below), WorkBC's count and how many came back.
- **Where:** Metro Vancouver (`metro`), `remote`, `other_bc`, `outside` or `unknown`, plus
  remote/hybrid/onsite where it's known. Each remote posting is listed with the reason.
- **Title match:** among Metro Vancouver and remote postings, `exact` (a search term appears
  as a phrase in the title, a strong match) or `loose` (possible match), with the possible ones
  listed so you can judge the terms.
- **Employers and job-board systems:** employers after merging name variants, Job Bank
  postings, the job-board system of every link, and the Ashby, Greenhouse and Lever boards
  (the ones Scout will read in task 1.8).

| Outcome | Meaning |
|---|---|
| `ok` / `empty` | Fine. `empty` means WorkBC has no postings for that term today. |
| `transient` | Timeouts or server errors, still failing after 2 retries. Try again later. |
| `restricted` | WorkBC refused us (401/403). Stop and check before trying again. |
| `structural` | The response wasn't what Scout expects: the API may have changed. |
| `incomplete` (note) | Fewer postings than WorkBC reported (hit the page limit, or jobs closed mid-sweep). |

## 14. Save sweeps to the database (Phase 1, box)

After pulling and rebuilding (see **Updating**, which also runs `db migrate`):

```bash
cd /srv/scout/code
docker compose --profile scout run --rm scout sweep
```

Same search and summary as the dry run, then a "saved to the database" section. The first
time, every posting is new. Run it again and nothing should change: `new 0, changed 0`,
everything "seen again", `not seen this time: 0`.

Likely duplicates (the same job under two WorkBC listings, employer-name variants or
different sites) share a `dup_group_id`; nothing is merged or deleted. See `v_duplicates`.

Every posting gets a trust score with its reasons, e.g. `{"+2 ats_board:ashby","+1 confirmed_on_board"}`.
Red flags (fees, SIN or bank details, cheque-cashing or reshipping duties, chat-app or free-email
contact, impossible pay, no employer name) push it below 0; those are listed in `v_suspicious`.
Nothing is hidden or deleted because of its score.

How closing jobs are handled: a posting is counted as "missed" only after a sweep in which
every term succeeded. After 3 misses in a row, once it has also been unseen for 24 hours, it
becomes `removed`; it is never deleted, and
it comes back as `reappeared` if WorkBC lists it again. `--term` runs and failed sweeps never
count anything as missed.

For a summary without SQL (counts, recent runs, problems, newest relevant jobs):

```bash
docker compose --profile scout run --rm scout status
```

Or look at the data with the read-only role, inside the db container:

```bash
cd /srv/scout/code
q() { docker compose --profile scout exec -T db psql -U scout_reader -d scout -c "$1"; }
q "SELECT title, employer, city, workplace, jump_url FROM v_new_relevant_7d LIMIT 20"
q "SELECT area, match_quality, count(*) FROM postings GROUP BY 1, 2 ORDER BY 1, 2"
q "SELECT ats, count(*) FROM postings GROUP BY 1 ORDER BY 2 DESC"
q "SELECT title, employer, city FROM v_review"
q "SELECT * FROM v_source_health"
q "SELECT dup_group_id, title, employer, jump_url FROM v_duplicates"
q "SELECT title, employer, trust_score, trust_reasons, jump_url FROM v_suspicious"
q "SELECT trust_score, count(*) FROM postings GROUP BY 1 ORDER BY 1"
q "SELECT id, started_at, ok, terms_ok, terms_failed FROM scout_runs ORDER BY id DESC LIMIT 5"
```

(Inside the container, local connections need no password. From Windows, use the SSH tunnel
from step 12 and the `scout_reader` password.)

## 15. Job-board details: Ashby, Greenhouse, Lever (Phase 1, box)

For postings whose link goes to an Ashby, Greenhouse or Lever board, Scout reads the board
itself (one request per board, one a second; no key needed). It adds the description, pay
range (Ashby and Lever) and remote/hybrid/onsite to those postings, and also saves other jobs
on the same boards that match a search term (all its words in the title, as WorkBC's search
does) and are in Metro Vancouver or remote in Canada.
Greenhouse links on a company's own site (`?gh_jid=`) don't name the board: Scout tries a few
names made from the employer's name and keeps the one where that exact job exists.

Try it first without writing anything:

```bash
cd /srv/scout/code
docker compose --profile scout run --rm scout enrich --dry-run
```

Then for real. `scout sweep` now does it after saving (add `--no-enrich` to skip), or on its own:

```bash
docker compose --profile scout run --rm scout enrich
docker compose --profile scout run --rm scout enrich --board lever:wmg     # one board
q "SELECT title, employer, workplace, pay_min, pay_max, pay_currency, found_via FROM v_new_relevant_7d WHERE pay_min IS NOT NULL"
q "SELECT name, status, consecutive_failures, last_success_at FROM sources WHERE kind = 'ats' ORDER BY name"
```

Jobs found only on a board (`found_via = 'ats'`) are removed after 3 runs in which their board
answered without them. A board that fails is retried next time and marks nothing missed.

---

## 16. Running on its own: timers and backups (Phase 2, box)

From here Scout sweeps twice a day and backs up its database every night without you.
Timers on the box start each job; each one runs in a fresh container and exits, as when you run
it by hand. Times (Vancouver): **sweep 06:40 and 16:40**, **backup 03:30**. A job missed while
the box was off runs soon after it starts again.

**Once:** the backup folder (yours; the containers can't write to it):

```bash
sudo install -d -o $(id -un) -g $(id -gn) -m 750 /srv/scout/backups
```

**A first backup by hand** (about 10 seconds):

```bash
cd /srv/scout/code
ops/backup.sh
cat /srv/scout/backups/status
```

Expect `backup: restore check ok: employers=95 … postings=181 …` and `result=ok`. Each backup
is loaded into a scratch database (`scout_restore_check`) and its row counts compared with the
live database; the scratch copy is then dropped. Kept: the newest 14 dumps and one from each of
the last 8 Sundays.

**Install the timers** (asks for your password; they run as you, not as root):

```bash
sudo ops/systemd/install.sh
systemctl list-timers 'scout-*'
```

Expect two lines, `scout-sweep.timer` and `scout-backup.timer`, with their next run times.

**Try one sweep through the timer's service** (about a minute; the command waits until it's
done):

```bash
sudo systemctl start scout-sweep.service
journalctl -u scout-sweep -n 30 --no-pager
docker compose --profile scout run --rm scout status
```

The journal shows the same output as a sweep by hand; `status` lists it under recent runs.
(If `journalctl` says you can't see the system's messages, put `sudo` in front.)

Day to day:

| To | Run |
|---|---|
| See when jobs run next, and last ran | `systemctl list-timers 'scout-*'` |
| Read a sweep's or backup's output | `journalctl -u scout-sweep --since today` (or `-u scout-backup`) |
| See whether the last run failed | `systemctl status scout-sweep.service scout-backup.service --no-pager` |
| Pause the sweeps (stays paused across reboots) | `sudo systemctl disable --now scout-sweep.timer` |
| Resume them | `sudo systemctl enable --now scout-sweep.timer` |
| Remove all Scout timers | `sudo ops/systemd/install.sh --remove` |

Only one sweep or enrichment runs at a time. If you start one by hand while a timer's sweep is
running, yours prints "Another sweep or enrichment is running; this one is skipped." Raw WorkBC
responses in `/srv/scout/logs/workbc` older than 30 days are deleted after each sweep.

To restore a backup (not needed day to day; a full restore drill is a Phase 5 task):
`docker compose --profile scout exec -T db pg_restore -U postgres -d scout --clean --if-exists < /srv/scout/backups/scout-<time>.dump`.
It replaces what's in the database with the backup's contents.

---

## Updating

When `main` on GitHub has changed (you edit it through the web UI or from your own machine):

```bash
cd /srv/scout/code
git status --short                # should print nothing; the box copy is never edited
git pull --ff-only
sudo install -o root -g root -m 644 agent/managed-settings/managed-settings.json /srv/scout/policy/
sudo install -o root -g root -m 755 agent/managed-settings/hooks/guard.sh /srv/scout/policy/hooks/
docker compose --profile agent build agent
agent/phase0/smoke.sh --offline
# Phase 1 on: rebuild the scout image and apply any new migrations.
docker compose --profile scout build scout
docker compose --profile scout run --rm scout db migrate
docker compose --profile scout run --rm scout db check
# Phase 2 on: only when the pull changed ops/systemd/ (git pull lists the changed files).
sudo ops/systemd/install.sh
```

The timers use the new code from their next run; `ops/backup.sh` runs straight from
`/srv/scout/code`. Your key, workspace and logs in `/srv/scout` are not touched. To go back if something breaks:
`git log --oneline` then `git checkout <commit>` (and `git checkout main` to return).

Results you want on GitHub (for example the numbers for `phase0-results.md`) go there by hand
through the web UI; nothing on the box is ever pushed.

## Starting over

To wipe Scout from the box and repeat this guide from step 2:

```bash
docker compose --profile scout down
docker ps -a --filter name=scout -q | xargs -r docker rm -f
docker image rm scout-agent:dev scout-runtime:dev
sudo rm -rf /srv/scout
```

To reset only the database (all data lost; new passwords), then repeat step 12:

```bash
docker compose --profile scout down
sudo rm -rf /srv/scout/pgdata /srv/scout/secrets/db-admin.env /srv/scout/secrets/db-roles.env
```

Revoke the DeepSeek key on the console if you're not reusing it.

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `env file … deepseek.env not found` | Step 4 not done, or wrong path. `SCOUT_HOME` defaults to `/srv/scout`. |
| "Not logged in" / "Please run /login" | Key not reaching the container. Check the file has `ANTHROPIC_AUTH_TOKEN=sk-…` with no quotes or spaces. |
| 401 / authentication error | Wrong or revoked key. |
| 402 / insufficient balance | Balance empty: top up, or wait for next month. |
| `git clone` asks for a username | The repo isn't public, or the URL is mistyped. The box never stores GitHub credentials. |
| `git pull` refuses (local changes / not fast-forward) | Something edited the box copy. `git stash` or `git checkout -- .`, then pull again. |
| `Permission denied (publickey)` on `git clone` | You used the SSH URL; use the `https://` one. |
| Session runs until the timeout | Check the newest `/srv/scout/logs/session-*.err`. Network: `docker compose --profile agent run --rm agent curl -sI https://api.deepseek.com`. Claude Code retries failed requests (capped at 3 by `CLAUDE_CODE_MAX_RETRIES` in `compose.yaml`). |
| A session never returns, even after the timeout | Something is reading the terminal. `run-session.sh` feeds sessions `</dev/null`; do the same if you run `docker compose run … claude -p` by hand under `timeout`. |
| `[claude-code:unrecognized_model] deepseek-flash` on stderr | Harmless: Claude Code doesn't know DeepSeek's model names. |
| "Managed settings document could not be parsed" | `/srv/scout/policy/managed-settings.json` isn't valid JSON; re-install from the repo (step 5). |
| `failed to bind host port 127.0.0.1:5432 … address already in use` | Another Postgres is running on the box, usually one installed with apt. Check with `sudo ss -ltnp 'sport = :5432'`. If it's unused (`sudo -u postgres psql -l` shows only postgres/template0/template1): `sudo systemctl disable --now postgresql postgresql@18-main` (use your version number), then `docker compose --profile scout up -d db` again. |
| `warning: not saving raw responses: can't …` | `/srv/scout/logs/workbc` is missing or not writable by uid 10002: run the `install -d` line in step 13. |
| `db` never becomes healthy | `docker compose --profile scout logs db`. "Permission denied" on the data folder: re-run the `chown 999:999` line in step 12. |
| `password authentication failed for user "scout_…"` | `db-roles.env` changed after the database was first created. Passwords are set only on first start: reset the database (Starting over). |
| `db check` fails with "migrations not applied" | `docker compose --profile scout run --rm scout db migrate` |
| `… was edited after it was applied` | A migration file changed on GitHub. Applied migrations are never edited; put the change in a new numbered file. |
| `systemctl list-timers` shows no `scout-` timers | `sudo ops/systemd/install.sh` (step 16), from `/srv/scout/code`. |
| A timer's run failed | `journalctl -u scout-sweep -n 50 --no-pager` (or `scout-backup`). `docker: permission denied … docker.sock`: your account isn't in the `docker` group (`groups`); the timers run as you. |
| `backup: FAILED: … doesn't exist` | Create the folder: the `install -d` line in step 16. |
| `backup: FAILED: the dump doesn't restore` | The dump is kept in `/srv/scout/backups` for a look; earlier dumps are untouched. Paste `journalctl -u scout-backup -n 50`. |
| `Permission denied` in `/workspace` | `sudo chown 10001 /srv/scout/workspace` |
| Can't read agent files on the host | They're owned by uid 10001; your group can read them if step 2 was followed. Otherwise `sudo ls /srv/scout/workspace`. |

## Not done yet (later phases)

- A firewall rule so the agent container can't reach your home LAN at the network level
  (today only the hook blocks LAN addresses by text). Acceptance test 10.
- The daily digest email, health alerts and the WSL2 sync job (rest of Phase 2,
  [phase2-plan.md](phase2-plan.md)).
- Host timer for agent sessions, `CLAUDE.md` for the agent and the agent's local git repo
  (Phase 3).
