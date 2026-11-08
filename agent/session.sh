#!/usr/bin/env bash
# One agent session: the Phase 3 routine (docs/phase3-plan.md §3). Runs on the host as shaw2012,
# by hand or (from task 3.4) by a timer.
#
#   agent/session.sh                 start only if there's work: an open task, or a source
#                                    needing attention ("repair"); otherwise skip, zero tokens
#   agent/session.sh --task 0000     run this task now (no gate)
#   agent/session.sh --force         run even if there's nothing to do
#   agent/session.sh --dry-run       gate and briefing only; no session
#   agent/session.sh --check-rules   a 1-turn session: can the agent see its locked rules?
#   agent/session.sh --pack          only the after-step: refresh agent-outbox (no LLM)
#
# Steps: lock -> gate -> briefing (logs/agent-briefing, mounted read-only at /briefing) ->
# before-step in the container -> Claude Code session -> after-step in the container, the
# only step that mounts /srv/scout/agent-outbox (agent.bundle, manifest, notes) ->
# logs/agent/last-session.json for the digest.
#
# Environment: SCOUT_HOME (/srv/scout), SCOUT_AGENT_TURNS (60), SCOUT_AGENT_TIMEOUT (1800 s),
# SCOUT_TASKS_DIR (agent/tasks next to this script).

set -uo pipefail

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
SCOUT_HOME=${SCOUT_HOME:-/srv/scout}
export SCOUT_HOME
TASKS=${SCOUT_TASKS_DIR:-$REPO_DIR/agent/tasks}
TURNS=${SCOUT_AGENT_TURNS:-60}
TIMEOUT=${SCOUT_AGENT_TIMEOUT:-1800}
WRAP_UP=$((TURNS > 15 ? TURNS - 10 : TURNS))
BRIEF=$SCOUT_HOME/logs/agent-briefing
OUTBOX=$SCOUT_HOME/agent-outbox
LOGS=$SCOUT_HOME/logs/agent
RULES_VERSION=scout-rules-1
COMPOSE=(docker compose -f "$REPO_DIR/compose.yaml")

mode=gate task=""
case ${1:-} in
  "") ;;
  --task) mode=task; task=${2:-}; [[ $task =~ ^[0-9]{4}$ ]] || { echo "session: --task needs a 4-digit id" >&2; exit 64; } ;;
  --force) mode=force ;;
  --dry-run) mode=dry ;;
  --check-rules) mode=rules ;;
  --pack) mode=pack ;;
  *) sed -n '2,18p' "$0"; exit 64 ;;
esac

mkdir -p "$BRIEF" "$LOGS" || exit 1
STAMP=$(date +%Y%m%d-%H%M%S)

# One session at a time (the timer and you can't overlap).
exec 9>"$LOGS/.lock"
if ! flock -n 9; then
  echo "session: another agent session is running; not starting"
  exit 0
fi

# Write logs/agent/last-session.json (and a line in sessions.log). Fields after the first
# two are key=value pairs; the session result file, if any, adds its own fields.
record() {
  local outcome=$1 detail=$2; shift 2
  python3 -I - "$LOGS" "$outcome" "$detail" "$task" "$@" <<'PY'
import json, os, sys, time
logs, outcome, detail, task, *rest = sys.argv[1:]
rec = {"time": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "outcome": outcome, "detail": detail,
       "task": task or None}
for kv in rest:
    k, _, v = kv.partition("=")
    if k == "result_file":
        try:
            if v.endswith(".jsonl"):
                r = None
                for line in reversed(open(v).read().splitlines()):
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("type") == "result":
                        r = e
                        break
            else:
                r = json.load(open(v))
        except Exception:
            r = None
        if isinstance(r, dict):
            rec.update({
                "subtype": r.get("subtype"), "is_error": r.get("is_error"),
                "turns": r.get("num_turns"), "api_error": r.get("api_error_status"),
                "duration_s": round((r.get("duration_ms") or 0) / 1000),
                "result": (r.get("result") or "")[:500],
            })
        rec["result_file"] = v
    else:
        rec[k] = int(v) if v.lstrip("-").isdigit() else v
if rec.get("subtype") == "error_max_turns":
    rec["outcome"] = "turn_limit"
elif rec["outcome"] == "ran" and rec.get("is_error"):
    rec["outcome"] = "failed"
tmp =os.path.join(logs, ".last-session.tmp")
with open(tmp, "w") as f:
    json.dump(rec, f, indent=1)
os.replace(tmp, os.path.join(logs, "last-session.json"))
with open(os.path.join(logs, "sessions.log"), "a") as f:
    f.write(json.dumps(rec, separators=(",", ":")) + "\n")
PY
}

open_tasks() {
  local f
  for f in "$TASKS"/[0-9][0-9][0-9][0-9]-*.md; do
    [[ -f $f ]] && grep -qx 'status: open' "$f" && basename "$f" | cut -c1-4
  done
}

# The no-LLM steps run in the agent container. Only the after-step gets the outbox.
agent_step() {
  local mount=()
  if [[ $1 == after ]]; then
    if [[ ! -d $OUTBOX || -L $OUTBOX ]]; then
      echo "session: $OUTBOX is missing; create it (box-setup §19 step 1)" >&2
      return 1
    fi
    mount=(-v "$OUTBOX:/outbox")
  fi
  "${COMPOSE[@]}" --profile agent run --rm -T --name "scout-agent-step-$1-$STAMP-$$" "${mount[@]}" \
    agent bash /src/main/agent/workspace-step.sh "$1"
}

if [[ $mode == pack ]]; then
  agent_step after
  exit
fi

# ---------------------------------------------------------------- rules check (1 turn)
if [[ $mode == rules ]]; then
  SCOUT_WORKDIR=/workspace SCOUT_SESSION_OUT="$LOGS/rules-$STAMP" "$REPO_DIR/agent/run-session.sh" \
    "Which rules version is named in the title of your Scout agent rules? Reply with only the version string. Use no tools." \
    3 120
  rc=$?
  if grep -q "$RULES_VERSION" "$LOGS/rules-$STAMP.json" 2>/dev/null; then
    echo "rules: PASS, the agent sees $RULES_VERSION"
    exit 0
  fi
  echo "rules: FAIL, the agent didn't name $RULES_VERSION (exit $rc); see $LOGS/rules-$STAMP.json"
  echo "  Is agent/managed-settings/CLAUDE.md installed in /srv/scout/policy/? (box-setup §19)"
  exit 1
fi

# ---------------------------------------------------------------- briefing
# Written by the host from the scout and db containers; the agent reads it at /briefing.
brief_write() {  # brief_write NAME < content
  local tmp="$BRIEF/.$1.tmp"
  cat >"$tmp" && mv -f "$tmp" "$BRIEF/$1"
}
status_ok=1
if ! "${COMPOSE[@]}" --profile scout run --rm -T scout status 2>"$LOGS/status.err" | brief_write status.txt \
   || ! grep -q '^== postings' "$BRIEF/status.txt"; then
  status_ok=0
fi
if [[ -f $SCOUT_HOME/logs/digests/latest.txt ]]; then
  brief_write digest.txt <"$SCOUT_HOME/logs/digests/latest.txt"
else
  echo "(no digest yet)" | brief_write digest.txt
fi
"${COMPOSE[@]}" --profile scout exec -T db psql -U scout_reader -d scout -X -q --csv -c "
  SELECT coalesce(ats, '') AS ats,
         coalesce(ats_board, substring(jump_url from '^https?://([^/?#]+)'), '') AS board,
         min(employer_name) AS employer,
         count(*) AS postings,
         count(*) FILTER (WHERE description IS NOT NULL) AS with_details,
         min(jump_url) AS example_link
  FROM postings WHERE status = 'active'
  GROUP BY 1, 2 ORDER BY 4 DESC, 1, 2" 2>>"$LOGS/status.err" | brief_write boards.csv

# "== sources needing attention: none" when all is well; anything else is work for the agent.
attention=0
if ((status_ok)) && grep -q '^== sources needing attention' "$BRIEF/status.txt" \
   && ! grep -q '^== sources needing attention: none' "$BRIEF/status.txt"; then
  attention=1
fi

# ---------------------------------------------------------------- gate
mapfile -t open < <(open_tasks)
case $mode in
  task)
    [[ -n $(compgen -G "$TASKS/$task-*.md") ]] || { echo "session: no task file $TASKS/$task-*.md" >&2; exit 64; }
    why="task $task, by hand" ;;
  force)
    task=${open[0]:-}
    [[ -z $task ]] && ((attention)) && task=repair
    why="forced" ;;
  gate | dry)
    if ((${#open[@]})); then
      task=${open[0]}; why="open task $task"
    elif ((attention)); then
      task=repair; why="a source needs attention"
    else
      why="nothing to do"
    fi ;;
esac
((status_ok)) || why="$why (scout status failed: see $LOGS/status.err)"
echo "session: $why; open tasks: ${open[*]:-none}; sources needing attention: $([[ $attention == 1 ]] && echo yes || echo no)"

if [[ $mode == dry ]]; then
  echo "session: dry run; briefing written to $BRIEF"
  exit 0
fi
if [[ $mode == gate && -z $task ]]; then
  record skipped "$why"
  exit 0
fi
if [[ $mode == gate && $status_ok == 0 && $task == repair ]]; then
  record skipped "$why"
  exit 0
fi

label=${task:-none}
cat <<EOF | brief_write session.txt
task: $label
task_file: $(compgen -G "$TASKS/$label-*.md" | sed "s|^$TASKS|/src/main/agent/tasks|" || true)
started: $(date --iso-8601=seconds)
turn_limit: $TURNS
wrap_up_by_turn: $WRAP_UP
time_limit_minutes: $((TIMEOUT / 60))
why: $why
EOF

# ---------------------------------------------------------------- before (no LLM)
if ! agent_step before; then
  record failed "before-step failed (see the output above)"
  exit 1
fi

# ---------------------------------------------------------------- session
prompt="Start a Scout agent session. Task: $label. Follow your Scout agent rules; /briefing/session.txt has the task and the limits."
SCOUT_WORKDIR=/workspace SCOUT_STREAM=1 SCOUT_SESSION_OUT="$LOGS/session-$STAMP" \
  "$REPO_DIR/agent/run-session.sh" "$prompt" "$TURNS" "$TIMEOUT"
rc=$?
result="$LOGS/session-$STAMP.jsonl"

# ---------------------------------------------------------------- after (no LLM)
agent_step after || echo "session: after-step failed; the outbox may be out of date" >&2

if ((rc == 124)); then
  record timeout "hit the ${TIMEOUT}s time limit" exit=$rc result_file="$result"
elif ((rc != 0)); then
  record failed "Claude Code exited $rc" exit=$rc result_file="$result"
else
  record ran "$why" exit=$rc result_file="$result"
fi
exit $rc
