#!/usr/bin/env bash
# Start one headless Claude Code session in the hardened agent container.
#
#   agent/run-session.sh "task text" [max_turns] [timeout_seconds]
#
# Defaults: 20 turns, 900 s. These two limits plus the prepaid DeepSeek
# balance are the only budget controls (plan §5.2).
#
# The session's JSON result goes to $SCOUT_HOME/logs/session-<time>.json
# (stderr next to it as .err). Exit code: Claude Code's, or 124 on timeout.
#
# Optional environment:
#   SCOUT_EXTRA_ENV="NAME=value"  one extra env var for the container (smoke test: invalid key)
#   SCOUT_WORKDIR=/workspace/...  start the session in this folder (default /workspace)
#   SCOUT_STREAM=1                save the full transcript (stream-json, one event per line,
#                                 file ends in .jsonl); the last line is the usual result
#   SCOUT_SESSION_OUT=/path/name  output path without extension (default logs/session-<time>)

set -uo pipefail

if [[ $# -lt 1 ]]; then
  sed -n '2,18p' "$0"; exit 64
fi

TASK=$1
MAX_TURNS=${2:-${SCOUT_MAX_TURNS:-20}}
TIMEOUT=${3:-${SCOUT_SESSION_TIMEOUT:-900}}

REPO_DIR=$(cd "$(dirname "$0")/.." && pwd)
SCOUT_HOME=${SCOUT_HOME:-/srv/scout}
LOG_DIR=${SCOUT_LOG_DIR:-$SCOUT_HOME/logs}
mkdir -p "$LOG_DIR"

STAMP=$(date +%Y%m%d-%H%M%S)
NAME="scout-agent-$STAMP-$$"
BASE=${SCOUT_SESSION_OUT:-$LOG_DIR/session-$STAMP}
format=(--output-format json)
OUT="$BASE.json"
if [[ "${SCOUT_STREAM:-0}" == 1 ]]; then
  format=(--output-format stream-json --verbose)
  OUT="$BASE.jsonl"
fi
ERR="$BASE.err"

# If the timeout fires, `docker compose run` dies but the container may not:
# always remove it by name on the way out.
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

extra=()
[[ -n "${SCOUT_EXTRA_ENV:-}" ]] && extra+=(-e "$SCOUT_EXTRA_ENV")
[[ -n "${SCOUT_WORKDIR:-}" ]] && extra+=(-w "$SCOUT_WORKDIR")

# stdin is /dev/null: `docker compose run` attaches stdin by default, and under
# `timeout` (a background process group) reading the terminal freezes it.
start=$(date +%s)
timeout --kill-after=15 "$TIMEOUT" \
  docker compose -f "$REPO_DIR/compose.yaml" --profile agent run --rm -T \
    --name "$NAME" "${extra[@]}" agent \
    claude -p "$TASK" --max-turns "$MAX_TURNS" "${format[@]}" \
  </dev/null >"$OUT" 2>"$ERR"
rc=$?
secs=$(( $(date +%s) - start ))

if [[ $rc -eq 124 || $rc -eq 137 ]]; then
  echo "session: TIMEOUT after ${secs}s (limit ${TIMEOUT}s) -> $OUT"
  exit 124
fi

# Short summary from the result (python3 ships with Ubuntu). For a transcript,
# the result is the last line with "type": "result".
python3 -I - "$OUT" "$rc" "$secs" <<'PY'
import json, sys
path, rc, secs = sys.argv[1], sys.argv[2], sys.argv[3]
r = None
try:
    with open(path) as f:
        text = f.read()
    if path.endswith(".jsonl"):
        for line in reversed(text.splitlines()):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "result":
                r = event
                break
    else:
        r = json.loads(text)
except Exception:
    r = None
if not isinstance(r, dict):
    print(f"session: exit {rc} after {secs}s, no JSON result (see {path.rsplit('.', 1)[0]}.err)")
    sys.exit()
print(f"session: exit {rc} after {secs}s | subtype={r.get('subtype')} "
      f"is_error={r.get('is_error')} turns={r.get('num_turns')}"
      + (f" api_error={r['api_error_status']}" if r.get("api_error_status") else "")
      + f" -> {path}")
res = (r.get("result") or "").strip().replace("\n", " ")
if res:
    print("result:", res[:300])
PY
exit $rc
