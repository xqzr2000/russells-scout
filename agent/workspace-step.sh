#!/usr/bin/env bash
# The no-LLM steps before and after an agent session (docs/phase3-plan.md §3).
# Runs INSIDE the agent container (uid 10001), started by agent/session.sh:
#
#   workspace-step.sh before   set up /workspace; clone or refresh the agent's repo from /src/main
#   workspace-step.sh after    pack the agent's branches and notes into /outbox
#
# It runs here, not on the host, because the agent controls its repo: a git config
# in a repo can make git run commands. The host never runs git on it.
#
# /outbox is /srv/scout/agent-outbox, mounted only for this step (never during a session),
# so the agent can't put anything there itself. The WSL2 sync downloads it. It's rebuilt each
# time and holds only regular files:
#   agent.bundle   every agent/* branch (git bundle; `git fetch agent.bundle` reads it)
#   manifest.txt   when; each branch with its head commit; and `pending=` for each branch that
#                  waits for review: commits beyond origin/main, and its task (agent/NNNN-…)
#                  not `status: done` on main. The digest lists only those.
#   notes/*.md     copies of /workspace/notes/*.md (regular files under 256 KB only)
#   guard.log      the last 200 guard-hook blocks (for the digest)

set -uo pipefail

WS=${SCOUT_WORKSPACE:-/workspace}
MAIN=${SCOUT_SRC_MAIN:-/src/main}
REPO=$WS/repo
OUT=${SCOUT_OUTBOX:-/outbox}
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
  [[ -d $OUT && -w $OUT ]] || { echo "workspace: $OUT is missing or not writable" >&2; exit 1; }
  local new=$OUT/.new
  rm -rf "$new"
  mkdir -p "$new/notes" || exit 1

  local branches=()
  if [[ -d $REPO/.git ]]; then
    mapfile -t branches < <(g -C "$REPO" for-each-ref --format='%(refname)' 'refs/heads/agent/')
  fi
  {
    echo "time=$(date --iso-8601=seconds)"
    echo "origin_main=$(g -C "$REPO" rev-parse --short origin/main 2>/dev/null || echo none)"
    local b name ahead id
    for b in "${branches[@]}"; do
      echo "branch=${b#refs/heads/} $(g -C "$REPO" log -1 --format='%h %cI %s' "$b")"
    done
    for b in "${branches[@]}"; do
      name=${b#refs/heads/}
      ahead=$(g -C "$REPO" rev-list --count "origin/main..$b" 2>/dev/null || echo 0)
      ((ahead > 0)) || continue
      if [[ $name =~ ^agent/([0-9]{4})- ]]; then
        id=${BASH_REMATCH[1]}
        if grep -qx 'status: done' "$MAIN"/agent/tasks/"$id"-*.md 2>/dev/null; then
          continue
        fi
      fi
      echo "pending=$name"
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

  local glog=$WS/.scout-logs/guard.log
  if [[ -f $glog && ! -L $glog ]]; then
    tail -n 200 -- "$glog" >"$new/guard.log"
  fi

  # Replace the outbox's contents (the folder itself is a mount point and stays).
  # rm acts on symlinks themselves, never their targets.
  find "$OUT" -mindepth 1 -maxdepth 1 ! -name .new -exec rm -rf -- {} +
  mv -- "$new"/* "$OUT"/
  rmdir -- "$new"
  echo "workspace: outbox has ${#branches[@]} branch(es)$([[ -f $OUT/agent.bundle ]] && echo ", agent.bundle $(stat -c %s "$OUT/agent.bundle") bytes")"
}

case ${1:-} in
  before) before ;;
  after) after ;;
  *) sed -n '2,15p' "$0"; exit 64 ;;
esac
