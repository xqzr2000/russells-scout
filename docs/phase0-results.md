# Phase 0 results

Fill in as you go. Plan: [architecture-plan.md](architecture-plan.md) §13.

## Setup (steps 1–4)

| Item | Value |
|---|---|
| Date | |
| Claude Code version | 2.1.310 (pinned in `agent/Dockerfile`) |
| Model / effort | `deepseek-flash` / `low` |
| Managed settings path | `/etc/claude-code/managed-settings.json` in the container (host: `/srv/scout/policy/`) |
| `smoke.sh --offline` | |
| `smoke.sh` (full) | |
| DeepSeek cost of one full smoke run | |
| Agent container peak memory (`docker stats`) | |
| Notes | |

## Capability tests (step 5)

| Task | Run 1 | Run 2 | Run 3 | Turns | Cost |
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
