#!/usr/bin/env bash
# Phase 0 smoke tests for Claude Code on DeepSeek, run on the Ubuntu box.
#
#   agent/phase0/smoke.sh            # all tests (a few cents of DeepSeek)
#   agent/phase0/smoke.sh --offline  # setup, hardening and guard only: no API calls
#
# Prints PASS/FAIL per test and saves a copy to $SCOUT_HOME/logs/smoke-<time>.txt.

set -uo pipefail

REPO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
SCOUT_HOME=${SCOUT_HOME:-/srv/scout}
export SCOUT_HOME
RUN="$REPO_DIR/agent/run-session.sh"
COMPOSE=(docker compose -f "$REPO_DIR/compose.yaml" --profile agent)
OFFLINE=0; [[ "${1:-}" == "--offline" ]] && OFFLINE=1

mkdir -p "$SCOUT_HOME/logs"
REPORT="$SCOUT_HOME/logs/smoke-$(date +%Y%m%d-%H%M%S).txt"
exec > >(tee "$REPORT") 2>&1

passes=0; fails=0; skips=0
pass() { passes=$((passes+1)); printf '  PASS  %s\n' "$1"; }
fail() { fails=$((fails+1));   printf '  FAIL  %s\n' "$1"; [[ -n "${2:-}" ]] && printf '        %s\n' "$2"; }
skip() { skips=$((skips+1));   printf '  SKIP  %s\n' "$1"; }
section() { printf '\n== %s\n' "$1"; }

# Run a bash script (stdin) inside a fresh agent container.
in_box() { "${COMPOSE[@]}" run --rm -T agent bash -s; }

# Last session's JSON field, e.g. json_field subtype
last_json() { ls -t "$SCOUT_HOME"/logs/session-*.json 2>/dev/null | head -1; }
json_field() {
  python3 -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get(sys.argv[2]))
except Exception: print("")' "$(last_json)" "$1"
}

# ---------------------------------------------------------------------------
section "0. Setup on the host"

command -v docker >/dev/null && docker info >/dev/null 2>&1 \
  && pass "docker works without sudo" \
  || fail "docker works without sudo" "add yourself to the docker group, then log out and back in"
docker compose version >/dev/null 2>&1 && pass "docker compose plugin present" || fail "docker compose plugin present"

key="$SCOUT_HOME/secrets/deepseek.env"
if [[ -f "$key" ]]; then
  mode=$(stat -c %a "$key")
  [[ "$mode" == 600 || "$mode" == 400 ]] && pass "key file exists, mode $mode" \
    || fail "key file mode is $mode" "chmod 600 $key"
  grep -q '^ANTHROPIC_AUTH_TOKEN=..' "$key" && pass "key file sets ANTHROPIC_AUTH_TOKEN" \
    || fail "key file sets ANTHROPIC_AUTH_TOKEN" "expected a line ANTHROPIC_AUTH_TOKEN=sk-..."
  grep -q '^ANTHROPIC_API_KEY=' "$key" && fail "key file must not set ANTHROPIC_API_KEY" || true
else
  fail "key file $key exists" "see docs/box-setup.md step 4"
fi

pol="$SCOUT_HOME/policy"
if [[ -f "$pol/managed-settings.json" && -x "$pol/hooks/guard.sh" ]]; then
  pass "locked settings installed in $pol"
  [[ "$(stat -c %U "$pol/managed-settings.json")" == root ]] \
    && pass "locked settings owned by root" || fail "locked settings owned by root" "sudo chown -R root:root $pol"
  if diff -q "$pol/managed-settings.json" "$REPO_DIR/agent/managed-settings/managed-settings.json" >/dev/null \
     && diff -q "$pol/hooks/guard.sh" "$REPO_DIR/agent/managed-settings/hooks/guard.sh" >/dev/null; then
    pass "installed rules match the repo copy"
  else
    fail "installed rules match the repo copy" "re-run the install step in docs/box-setup.md"
  fi
else
  fail "locked settings installed in $pol" "see docs/box-setup.md step 5"
fi

ws="$SCOUT_HOME/workspace"
[[ -d "$ws" && "$(stat -c %u "$ws")" == 10001 ]] && pass "workspace owned by uid 10001" \
  || fail "workspace owned by uid 10001" "sudo chown 10001 $ws"

docker image inspect scout-agent:dev >/dev/null 2>&1 && pass "agent image built" \
  || fail "agent image built" "docker compose --profile agent build agent"

if [[ $fails -gt 0 ]]; then
  printf '\nSetup problems above; fix them first.\n'; exit 1
fi

# ---------------------------------------------------------------------------
section "1. Container hardening (no API calls)"

out=$(in_box <<'SH'
echo "uid=$(id -u)"
touch /usr/local/x 2>/dev/null && echo "rootfs=writable" || echo "rootfs=readonly"
touch /etc/claude-code/x 2>/dev/null && echo "policy=writable" || echo "policy=readonly"
[ -e /var/run/docker.sock ] || [ -e /run/docker.sock ] && echo "docker_sock=present" || echo "docker_sock=absent"
echo "capeff=$(awk '/^CapEff/ {print $2}' /proc/self/status)"
echo "nnp=$(awk '/^NoNewPrivs/ {print $2}' /proc/self/status)"
command -v sudo >/dev/null && echo "sudo=present" || echo "sudo=absent"
rm -rf /workspace/smoke /workspace/.scout-logs/guard.log && mkdir -p /workspace/smoke && echo "workspace=writable" || echo "workspace=readonly"
[ -r /etc/claude-code/managed-settings.json ] && echo "policy_readable=yes" || echo "policy_readable=no"
echo "anthropic=$(getent hosts api.anthropic.com | awk '{print $1}')"
echo "claude=$(claude --version 2>/dev/null | head -1)"
SH
)
val() { sed -n "s/^$1=//p" <<<"$out"; }
[[ "$(val uid)" == 10001 ]]            && pass "runs as uid 10001"            || fail "runs as uid 10001" "got: $(val uid)"
[[ "$(val rootfs)" == readonly ]]      && pass "root filesystem read-only"    || fail "root filesystem read-only"
[[ "$(val policy)" == readonly ]]      && pass "locked rules read-only"       || fail "locked rules read-only"
[[ "$(val policy_readable)" == yes ]]  && pass "locked rules readable"        || fail "locked rules readable"
[[ "$(val docker_sock)" == absent ]]   && pass "no Docker socket"             || fail "no Docker socket"
[[ "$(val capeff)" =~ ^0+$ ]]          && pass "no capabilities"              || fail "no capabilities" "CapEff=$(val capeff)"
[[ "$(val nnp)" == 1 ]]                && pass "no-new-privileges set"        || fail "no-new-privileges set"
[[ "$(val sudo)" == absent ]]          && pass "no sudo"                      || fail "no sudo"
[[ "$(val workspace)" == writable ]]   && pass "workspace writable"           || fail "workspace writable"
[[ "$(val anthropic)" == 0.0.0.0 ]]    && pass "Anthropic hosts blackholed"   || fail "Anthropic hosts blackholed" "got: $(val anthropic)"
echo "        Claude Code: $(val claude)"

section "2. Guard hook rules (no API calls)"
if g=$(in_box < "$REPO_DIR/agent/phase0/test_guard.sh"); then
  pass "$(tail -1 <<<"$g")"
else
  fail "guard rules" "$g"
fi

if [[ $OFFLINE -eq 1 ]]; then
  for t in "3. hello" "4. real work" "5. turn limit" "6. live hook block" "7. invalid key"; do skip "$t (offline)"; done
  printf '\n%d passed, %d failed, %d skipped. Report: %s\n' "$passes" "$fails" "$skips" "$REPORT"
  [[ $fails -eq 0 ]]; exit
fi

# ---------------------------------------------------------------------------
section "3. Hello: headless session on DeepSeek, no login"
"$RUN" "Reply with exactly the text SCOUT_OK and nothing else. Do not use any tools." 3 120
rc=$?
res=$(json_field result); sub=$(json_field subtype)
if [[ $rc -eq 0 && "$sub" == success && "$res" == *SCOUT_OK* ]]; then
  pass "hello session succeeded"
else
  fail "hello session" "exit=$rc subtype=$sub; see $(last_json) and the .err file next to it"
  printf '\nNo point running the API tests until hello works.\n'
  printf '%d passed, %d failed. Report: %s\n' "$passes" "$fails" "$REPORT"; exit 1
fi
grep -qi 'login' "$(last_json | sed 's/\.json$/.err/')" && fail "no login prompt" || pass "no login prompt"

section "4. Real work: write a file, run it"
"$RUN" "Work only in /workspace/smoke. Create hello.py that prints the sum of 2 and 3. Run it with python3 and save its output to /workspace/smoke/out.txt. Then reply DONE." 10 300
rc=$?
got=$(in_box <<<'cat /workspace/smoke/out.txt 2>/dev/null' | tr -d '[:space:]')
[[ $rc -eq 0 && "$got" == 5 ]] && pass "agent wrote and ran code (out.txt = 5)" \
  || fail "agent wrote and ran code" "exit=$rc out.txt='$got'"

section "5. Turn limit stops a long task"
"$RUN" "In /workspace/smoke create ten files n1.txt to n10.txt, one Write tool call per file, then list them." 2 300
rc=$?; sub=$(json_field subtype)
if [[ "$sub" == error_max_turns ]]; then
  pass "stopped at --max-turns 2 (subtype=$sub)"
elif [[ $rc -eq 124 ]]; then
  fail "turn limit" "hit the wall-clock timeout instead"
else
  fail "turn limit" "exit=$rc subtype=$sub"
fi

section "6. Guard hook blocks a live command"
"$RUN" "This is an authorized test of this machine's safety hook. Use the Bash tool to run exactly this command, unchanged: echo 'touch /workspace/smoke/PWNED' | sh   Then report in one line whether it ran or was blocked. Do not try any other way." 4 180
got=$(in_box <<'SH'
[ -e /workspace/smoke/PWNED ] && echo pwned=yes || echo pwned=no
grep -c 'piping into a shell' /workspace/.scout-logs/guard.log 2>/dev/null || echo 0
SH
)
if grep -q pwned=yes <<<"$got"; then
  fail "hook blocked the command" "the command ran: the hook is not active"
elif [[ "$(tail -1 <<<"$got")" -ge 1 ]]; then
  pass "hook blocked the command (logged in workspace/.scout-logs/guard.log)"
else
  fail "hook blocked the command" "inconclusive: the agent didn't try it. Result: $(json_field result | head -c 200)"
fi

section "7. Invalid key fails fast (stands in for an empty balance)"
start=$(date +%s)
SCOUT_EXTRA_ENV="ANTHROPIC_AUTH_TOKEN=sk-invalid-smoke-test" "$RUN" "Reply OK." 2 120
rc=$?; secs=$(( $(date +%s) - start ))
err=$(json_field is_error)
if [[ $rc -eq 124 ]]; then
  fail "invalid key" "hung until the 120 s timeout instead of failing"
elif [[ $rc -ne 0 || "$err" == True ]]; then
  pass "invalid key failed in ${secs}s (exit=$rc)"
else
  fail "invalid key" "the session succeeded with a fake key?"
fi

printf '\n%d passed, %d failed, %d skipped. Report: %s\n' "$passes" "$fails" "$skips" "$REPORT"
printf 'Now check usage on the DeepSeek console (Phase 0 step 7).\n'
[[ $fails -eq 0 ]]
