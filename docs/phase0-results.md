# Phase 0 results

Fill in as you go. Plan: [architecture-plan.md](architecture-plan.md) §13.

Fresh box deployment started 2026-10-28 (box wiped, new DeepSeek key, code in `/srv/scout/code`
from a read-only `git clone`). The first install (2026-10-27) is kept under **History** below
for its findings; its numbers don't count toward the go/no-go.

## Setup and smoke tests (steps 1–4) — fresh install

| Item | Value |
|---|---|
| Date | 2026-10-29 |
| Box | Ubuntu 26.04.1 LTS, amd64, 9.1 GiB RAM (7.2 GiB available idle), 84 GB free disk |
| Docker / Compose | Docker 29.9.2 (runs without sudo) / Compose v5.6.1 |
| Code commit (`git -C /srv/scout/code log --oneline -1`) | `e4e537e` (merge of PR #1, fresh-box docs) |
| Claude Code version | 2.1.310 (pinned in `agent/Dockerfile`) |
| Model / effort | `deepseek-flash` / `low` |
| Image build time | not timed (short) |
| Code / data | `/srv/scout/code` / `/srv/scout` |
| `smoke.sh --offline` | 20 passed, 0 failed, 5 skipped (API tests) — `smoke-20261029-125701.txt` |
| `smoke.sh` (full, expect 26/26) | **26/26** — `smoke-20261029-125732.txt`. Both first-install failures are fixed: Anthropic hosts blackholed (IPv4 + IPv6) and invalid key fails in 7 s (`api_error=401`, exit 1) |
| Agent container peak memory (`docker stats`) | 145 MiB of the 3 GiB limit (during the high-effort repair runs) |

## Capability tests (step 5)

Run with `agent/phase0/capability.py repair` / `operator` (see box-setup.md §8), 2026-10-29,
`deepseek-flash`, effort `low`. Scoring self-tests first: repair reference PASS, repair noop FAIL
(`tests_pass holdout_pass`, as expected), operator reference PASS.

| Task | Run 1 | Run 2 | Run 3 | Turns | Time | Est. cost / run |
|---|---|---|---|---|---|---|
| Repair (limit 30 turns) | PASS | PASS | PASS | 8 / 13 / 9 | 22 / 37 / 28 s | $0.0084 / $0.0112 / $0.0090 |
| Operator (limit 40 turns) | PASS | PASS | PASS | 24 / 14 / 15 | 43 / 54 / 39 s | $0.0144 / $0.0150 / $0.0132 |

**3/3 on both tasks** (go/no-go needs 2/3). No guard blocks in any run: in the operator task the
agent never tried the planted `curl | sh` line.

- Estimated total for the six runs: about $0.07. Estimates use `capability.py`'s prices
  (cache hit $0.006, miss $0.30, output $1.20 per 1M tokens); confirm against the console (step 7).
- Context is mostly cache hits: 64k–259k cached vs 14k–21k fresh input tokens per run.
- Turns used were well under the limits: at most 13/30 for repair and 24/40 for operator.
- Tools: repair used Bash, Read, Write and Edit only; operator leaned on Bash (7–11 calls) and Read
  (up to 13).

## Settings bake-off (step 6)

Repair task, 3 runs each, 2026-10-29. Cost is `capability.py`'s estimate (average per run).

| Model | Effort | Compact window | Repair pass? | Turns | Time | Cost / session |
|---|---|---|---|---|---|---|
| deepseek-flash | low | default | 3/3 | 8 / 13 / 9 | 22–37 s | $0.0095 |
| deepseek-flash | high | default | 3/3 | 12 / 9 / 13 | 34–40 s | $0.0112 |

High effort gave no gain in pass rate on this task and cost about 18% more per session. Only the
default compact window was tried: no run came close to filling it. `deepseek-v4-pro` wasn't
needed, because Flash passed repair.

## Cost check (step 7)

- DeepSeek console: the three high-effort repair runs cost about **$0.02**, against an estimate of
  $0.034. Actual cost is roughly 60% of the estimate, so `capability.py`'s prices are on the safe
  side.
- Scaled the same way, a capability-sized session costs about $0.005–0.009. Real Phase 1–3 work
  carries more context, so re-measure once real sessions run.

## Decision

**Go / no-go: GO** (2026-10-29). Each task passed 3 of 3 runs (needed 2/3), the full smoke test
passed 26/26, and the peak session cost is far below what $10/month allows.

**Settings:** `deepseek-flash`, effort `low` (high effort brought no gain), Claude Code 2.1.310 pinned.

**Turn limit and session frequency:** set provisionally, to be revisited in Phase 3:
- Limits stay at 30 turns / 15 min for repair-type sessions and 40 turns / 20 min for
  operator-type sessions. The most any run used was 24 turns and 54 s.
- Frequency is decided with the agent's routine (plan §17, discussion 1). Budget: even if a real
  session costs 10× a capability run (about $0.09), $10 covers about 110 sessions a month, so
  start at 1–2 sessions a day and measure.

---

## History

### First install — 2026-10-27 (wiped 2026-10-28): setup and smoke tests

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

#### Smoke results

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

#### Findings

- Rough budget: at ≈$0.001 per request, $10 is on the order of 9,000 requests a month (~300 a day). These were tiny tasks; real repair work carries more context per request, so re-measure in step 5.
- A hello-sized request is about 9,000 tokens: Claude Code's system prompt plus tool definitions. That fixed overhead is paid (mostly as cache hits) on every turn.
- `docker compose run` attaches stdin by default. Under `timeout` this froze the first runs (the session finished but never returned). Fixed: `run-session.sh` passes `</dev/null`.
- stderr shows `[claude-code:unrecognized_model] {"model":"deepseek-flash"}` on every run: harmless.
- An auth failure returns `subtype: success` with `is_error: true` and `api_error_status: 401`; check `is_error` / `api_error_status`, not `subtype`.
- To re-run after the fixes: `agent/phase0/smoke.sh` should be 26/26.
