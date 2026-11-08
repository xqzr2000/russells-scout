#!/usr/bin/env bash
# The no-LLM steps before and after an agent session (docs/phase3-plan.md §3).
# Runs INSIDE the agent container (uid 10001), started by agent/session.sh:
#
#   workspace-step.sh before   set up /workspace; clone or refresh the agent's repo from /src/main
#   workspace-step.sh after    pack the agent's branches and notes into /workspace/outbox
#
# It runs here, not on the host, because the agent controls its repo: a git config
# in a repo can make git run commands. The host never runs git on it.
#
# outbox/ is rebuilt from scratch each time and holds only regular files:
#   agent.bundle   every agent/* branch (git bundle; `git fetch agent.bundle` reads it)
#   manifest.txt   when, and each branch with its head commit
#   notes/*.md     copies of /workspace/notes/*.md (regular files under 256 KB only)

set -uo pipefail

WS=${SCOUT_WORKSPACE:-/workspace}
MAIN=${SCOUT_SRC_MAIN:-/src/main}
REPO=$WS/repo
OUT=$WS/outbox
MAX_NOTE=262144

# /src/main belongs to another user (shaw2012); git refuses that without safe.directory.
# A local clone or fetch drops `git -c` settings before starting upload-pack in /src/main,
# so the setting goes in a throwaway global config file instead. Hooks are switched off:
# none of the agent's repo hooks run in these steps.
GIT_CONFIG_GLOBAL=$(mktemp) || exit 1
export GIT_CONFIG_GLOBAL
trap 'rm -f "$GIT_CONFIG_GLOBAL"' EXIT
git config --global safe.directory '*'
g() { git -c core.hooksPath=/dev/null "$@"; }

before() {
  mkdir -p "$WS/notes" "$WS/scratch" || exit 1
  if [[ ! -d $REPO/.git ]]; then
    if [[ -e $REPO ]]; then
      echo "workspace: $REPO exists but isn't a git repo; leaving it alone" >&2
      exit 1
    fi
    g clone --quiet --no-local "$MAIN" "$REPO" || { echo "workspace: clone failed" >&2; exit 1; }
    echo "workspace: cloned main into $REPO"
  else
    # Explicit source and refspec: whatever the repo's own remote config says.
    g -C "$REPO" fetch --quiet --no-tags "$MAIN" "+refs/heads/main:refs/remotes/origin/main" \
      || { echo "workspace: fetch of main failed" >&2; exit 1; }
  fi
  echo "workspace: origin/main at $(g -C "$REPO" rev-parse --short origin/main)"
}

after() {
  local new=$WS/.outbox.new old=$WS/.outbox.old
  rm -rf "$new" "$old"
  mkdir -p "$new/notes" || exit 1

  local branches=()
  if [[ -d $REPO/.git ]]; then
    mapfile -t branches < <(g -C "$REPO" for-each-ref --format='%(refname)' 'refs/heads/agent/')
  fi
  {
    echo "time=$(date --iso-8601=seconds)"
    echo "origin_main=$(g -C "$REPO" rev-parse --short origin/main 2>/dev/null || echo none)"
    for b in "${branches[@]}"; do
      echo "branch=${b#refs/heads/} $(g -C "$REPO" log -1 --format='%h %cI %s' "$b")"
    done
  } >"$new/manifest.txt"

  if ((${#branches[@]})); then
    if ! g -C "$REPO" bundle create "$new/agent.bundle" "${branches[@]}" 2>"$new/bundle.err"; then
      echo "workspace: bundle failed: $(head -c 300 "$new/bundle.err")" >&2
      rm -f "$new/agent.bundle"
    fi
  fi
  rm -f "$new/bundle.err"

  local f
  for f in "$WS"/notes/*.md; do
    [[ -f $f && ! -L $f ]] || continue
    (($(stat -c %s "$f") <= MAX_NOTE)) || continue
    cp -- "$f" "$new/notes/"
  done

  # Swap in the new outbox. rm and mv act on symlinks themselves, never their targets.
  [[ -e $OUT || -L $OUT ]] && mv -- "$OUT" "$old"
  mv -- "$new" "$OUT"
  rm -rf "$old"
  echo "workspace: outbox has ${#branches[@]} branch(es)$([[ -f $OUT/agent.bundle ]] && echo ", agent.bundle $(stat -c %s "$OUT/agent.bundle") bytes")"
}

case ${1:-} in
  before) before ;;
  after) after ;;
  *) sed -n '2,15p' "$0"; exit 64 ;;
esac
