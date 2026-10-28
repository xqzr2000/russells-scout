# Phase 0 results

Fill in as you go. Plan: [architecture-plan.md](architecture-plan.md) §13.

## Setup and smoke tests (steps 1–4) — 2026-10-27

| Item | Value |
|---|---|
| Box | Ubuntu 26.04, amd64, 9.1 GiB RAM (7.3 GiB available idle), 74 GB free disk |
| Docker | runs without sudo; Compose v5.6.1 |
| Claude Code version | 2.1.310 (pinned in `agent/Dockerfile`) |
| Model / effort | `deepseek-flash` / `low` |
| Image build | 79 s first build |
| Managed settings path | `/etc/claude-code/managed-settings.json` in the container (host: `/srv/scout/policy/`, root-owned, read-only mount) |
| Code / data | `~/Documents/Project_Scout` / `/srv/scout` |
| `smoke.sh` (full) | 24 pass, 2 fail (both fixed afterwards, see below) |
| DeepSeek cost, whole evening (1 stuck + 1 full smoke run, 1 debug run) | $0.01 · 9 API requests · 87,002 tokens (≈9.7k tokens and ≈$0.001 per request) |
| Agent container peak memory (`docker stats`) | _not measured yet_ |

### Smoke results

| Test | Result |
|---|---|
| Setup (key file, locked rules, workspace, image) | pass |
| Hardening: uid 10001, read-only root, locked rules read-only, no Docker socket, no capabilities, no-new-privileges, no sudo | pass |
| Anthropic hosts blackholed | **fail**: only IPv4 was blackholed; lookup returned a real IPv6 address. Fixed in `compose.yaml` (IPv6 entries) |
| Guard rules (46 offline cases) | pass |
| 3. Hello | pass: `SCOUT_OK` in 2 s, no login prompt, 1 turn |
| 4. Real work | pass: wrote and ran `hello.py` in 5 s, 3 turns |
| 5. Turn limit | pass: `error_max_turns` at `--max-turns 2` in 6 s |
| 6. Live hook block | pass: agent ran `echo … \| sh`, guard blocked it, agent reported the block |
| 7. Invalid key | **fail**: ran to the 120 s timeout. Cause: Claude Code retries the DeepSeek 401 up to 10 times. With `CLAUDE_CODE_MAX_RETRIES=1` it fails in 2.8 s with `api_error_status: 401`. Fixed: `CLAUDE_CODE_MAX_RETRIES=3` in `compose.yaml` |

### Findings

- Rough budget: at ≈$0.001 per request, $10 is on the order of 9,000 requests a month (~300 a day). These were tiny tasks; real repair work carries more context per request, so re-measure in step 5.
- A hello-sized request is about 9,000 tokens: Claude Code's system prompt plus tool definitions. That fixed overhead is paid (mostly as cache hits) on every turn.
- `docker compose run` attaches stdin by default. Under `timeout` this froze the first runs (the session finished but never returned). Fixed: `run-session.sh` passes `</dev/null`.
- stderr shows `[claude-code:unrecognized_model] {"model":"deepseek-flash"}` on every run: harmless.
- An auth failure returns `subtype: success` with `is_error: true` and `api_error_status: 401`; check `is_error` / `api_error_status`, not `subtype`.
- To re-run after the fixes: `agent/phase0/smoke.sh` should be 26/26.

## Capability tests (step 5)

Run with `agent/phase0/capability.py repair` / `operator` (see box-setup.md §9).
Copy the per-run lines from the screen or `/srv/scout/logs/capability.csv`.

| Task | Run 1 | Run 2 | Run 3 | Turns | Est. cost / run |
|---|---|---|---|---|---|
| Repair | | | | | |
| Operator | | | | | |

## Settings bake-off (step 6)

| Model | Effort | Compact window | Repair pass? | Cost / session |
|---|---|---|---|---|
| deepseek-flash | low | default | | |
| deepseek-flash | high | default | | |

## Decision

Go / no-go:

Turn limit and session frequency:
