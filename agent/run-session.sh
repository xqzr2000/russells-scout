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
# Optional: SCOUT_EXTRA_ENV="NAME=value" passes one extra env var into the
# container (the smoke test uses it to try an invalid key).

set -uo pipefail

if [[ $# -lt 1 ]]; then
  sed -n '2,13p' "$0"; exit 64
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
OUT="$LOG_DIR/session-$STAMP.json"
ERR="$LOG_DIR/session-$STAMP.err"

# If the timeout fires, `docker compose run` dies but the container may not:
# always remove it by name on the way out.
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

extra=()
[[ -n "${SCOUT_EXTRA_ENV:-}" ]] && extra=(-e "$SCOUT_EXTRA_ENV")

start=$(date +%s)
timeout --kill-after=15 "$TIMEOUT" \
  docker compose -f "$REPO_DIR/compose.yaml" --profile agent run --rm -T \
    --name "$NAME" "${extra[@]}" agent \
    claude -p "$TASK" --max-turns "$MAX_TURNS" --output-format json \
  </dev/null >"$OUT" 2>"$ERR"
rc=$?
secs=$(( $(date +%s) - start ))

if [[ $rc -eq 124 || $rc -eq 137 ]]; then
  echo "session: TIMEOUT after ${secs}s (limit ${TIMEOUT}s) -> $OUT"
  exit 124
fi

# Short summary from the JSON result (python3 ships with Ubuntu).
python3 - "$OUT" "$rc" "$secs" <<'PY'
import json, sys
path, rc, secs = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    r = json.load(open(path))
except Exception:
    print(f"session: exit {rc} after {secs}s, no JSON result (see {path[:-5]}.err)")
    sys.exit()
print(f"session: exit {rc} after {secs}s | subtype={r.get('subtype')} "
      f"is_error={r.get('is_error')} turns={r.get('num_turns')} -> {path}")
res = (r.get("result") or "").strip().replace("\n", " ")
if res:
    print("result:", res[:300])
PY
exit $rc
