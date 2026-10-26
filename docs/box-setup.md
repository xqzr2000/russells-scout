# Box setup: Claude Code on DeepSeek (Phase 0, steps 1–4)

How to get the agent container running on the Ubuntu box, working from WSL2.
Design background: [architecture-plan.md](architecture-plan.md) §5 and §13.

Commands marked **WSL2** run in your WSL2 terminal on Windows.
Commands marked **box** run on the Ubuntu box (over `ssh scoutbox`).

## What you end up with

```
/srv/scout/
├── repo/                    unzipped copy of this repo (yours, plain files)
├── secrets/deepseek.env     your DeepSeek key, mode 600, never in git
├── policy/                  live locked rules, root-owned, mounted read-only into the agent
│   ├── managed-settings.json
│   └── hooks/guard.sh
├── workspace/               the agent's only writable folder (uid 10001)
└── logs/                    session results and smoke-test reports
```

---

## 0. An SSH shortcut (WSL2, once)

If you don't have one already, add this to `~/.ssh/config` in WSL2 (use your box's address and key):

```
Host scoutbox
    HostName 192.168.x.x
    User russell
    IdentityFile ~/.ssh/id_ed25519
```

Check: `ssh scoutbox 'hostname; docker --version'`.

## 1. Download the repo zip (Windows)

On GitHub, open `russells-scout` → **Code → Download ZIP**.
It lands in your Downloads folder as `russells-scout-main.zip`.

## 2. Copy it to the box (WSL2)

```bash
WINUSER=$(cmd.exe /c 'echo %USERNAME%' 2>/dev/null | tr -d '\r')
scp "/mnt/c/Users/$WINUSER/Downloads/russells-scout-main.zip" scoutbox:/tmp/
```

## 3. Folders and code (box)

```bash
ssh scoutbox
```

First time only:

```bash
sudo apt-get install -y unzip            # if missing
sudo mkdir -p /srv/scout/{repo,secrets,policy/hooks,workspace,logs}
sudo chown russell:russell /srv/scout/repo /srv/scout/secrets /srv/scout/logs
sudo chmod 700 /srv/scout/secrets
# Agent's folder: owned by the container user (10001), shared with your group so you can read it.
sudo chown 10001:$(id -g) /srv/scout/workspace
sudo chmod 2775 /srv/scout/workspace
```

Every time you install a new zip:

```bash
rm -rf /tmp/scout-unzip && unzip -q /tmp/russells-scout-main.zip -d /tmp/scout-unzip
rm -rf /srv/scout/repo && mv /tmp/scout-unzip/russells-scout-main /srv/scout/repo
chmod +x /srv/scout/repo/agent/*.sh /srv/scout/repo/agent/phase0/*.sh \
         /srv/scout/repo/agent/managed-settings/hooks/*.sh
cd /srv/scout/repo
```

`/srv/scout/repo` is just your copy of the code. The agent's own git repo (local
commits, pulled by WSL2) comes later, in Phase 3.

## 4. DeepSeek key (box)

On platform.deepseek.com: top up $10, auto top-up **off**, create an API key.
Then, on the box (the key isn't echoed and doesn't go into shell history):

```bash
read -rsp "DeepSeek key: " K; echo
printf 'ANTHROPIC_AUTH_TOKEN=%s\n' "$K" > /srv/scout/secrets/deepseek.env; unset K
chmod 600 /srv/scout/secrets/deepseek.env
```

Don't add `ANTHROPIC_API_KEY`: it conflicts with `ANTHROPIC_AUTH_TOKEN`.
See [`agent/env.example`](../agent/env.example).

## 5. Install the locked rules (box)

The agent reads these but can never change them: they're root-owned on the host
and mounted read-only. Re-run this whenever `agent/managed-settings/` changes
(the smoke test tells you if the installed copy is out of date).

```bash
cd /srv/scout/repo
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

## 6. Docker checks (box, once)

```bash
docker info >/dev/null && echo "docker ok"          # must work without sudo
docker compose version                               # needs the compose plugin
```

If `docker info` needs sudo: `sudo usermod -aG docker russell`, then log out and back in.
If `docker compose` is missing: `sudo apt-get install -y docker-compose-v2`
(or `docker-compose-plugin` if Docker came from Docker's own apt repo).

## 7. Build the agent image (box)

```bash
cd /srv/scout/repo
docker compose --profile agent build agent      # 3–5 minutes the first time
```

## 8. Smoke tests (box)

Free first (no API calls):

```bash
agent/phase0/smoke.sh --offline
```

This checks setup, the container hardening (uid 10001, read-only root, no capabilities,
no Docker socket, no sudo, Anthropic hosts blackholed) and 46 guard-rule cases.

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

A report is saved to `/srv/scout/logs/smoke-<time>.txt`; each session's JSON result and
stderr are in `/srv/scout/logs/session-*.json` / `.err`.

Test 6 can come back "inconclusive" if the model declines to run the command at all.
Run it again; if it keeps declining, that's fine for today (the offline guard tests
still prove the rules), but note it.

## 9. Try a session yourself (box)

```bash
agent/run-session.sh "List the files in /workspace and describe them in two sentences." 5 120
```

Arguments: task, max turns (default 20), timeout in seconds (default 900).
Effort level defaults to `low`; try `SCOUT_EFFORT=high agent/run-session.sh …` to compare.

## 10. Check the cost (browser)

After the smoke run, open the DeepSeek console's usage page and note what the run cost.
That's Phase 0 step 7 — used once to set turn limits and how often sessions run.
Write it down in [phase0-results.md](phase0-results.md).

## 11. Get the logs back to WSL2 (WSL2)

```bash
mkdir -p ~/scout-logs && scp 'scoutbox:/srv/scout/logs/*' ~/scout-logs/
```

Or open the box in **VS Code → Remote-SSH → scoutbox** and browse `/srv/scout/`.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `env file … deepseek.env not found` | Step 4 not done, or wrong path. `SCOUT_HOME` defaults to `/srv/scout`. |
| "Not logged in" / "Please run /login" | Key not reaching the container. Check the file has `ANTHROPIC_AUTH_TOKEN=sk-…` with no quotes or spaces. |
| 401 / authentication error | Wrong or revoked key. |
| 402 / insufficient balance | Balance empty: top up, or wait for next month. |
| Session runs until the timeout | Usually network: `docker compose --profile agent run --rm agent curl -sI https://api.deepseek.com` |
| "Managed settings document could not be parsed" | `/srv/scout/policy/managed-settings.json` isn't valid JSON; re-install from the repo (step 5). |
| `Permission denied` in `/workspace` | `sudo chown 10001 /srv/scout/workspace` |
| Can't read agent files on the host | They're owned by uid 10001; your group can read them if step 3 was followed. Otherwise `sudo ls /srv/scout/workspace`. |

## Not done yet (later phases)

- A firewall rule so the agent container can't reach your home LAN at the network level
  (today only the hook blocks LAN addresses by text). Acceptance test 10.
- Host timer for scheduled sessions, `CLAUDE.md` for the agent, the agent's local git
  repo and the WSL2 sync job (Phases 2–3).
