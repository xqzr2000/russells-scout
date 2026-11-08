#!/usr/bin/env bash
# Project Scout PostToolUse hook: the agent's turn budget (docs/phase3-plan.md §2.12).
#
# Claude Code doesn't tell the model how many turns it has used, so a rule like "wrap up by
# turn 50" can't be followed on its own. This hook counts tool calls in the session and,
# near the limit, tells the agent. Exit 2 from a PostToolUse hook shows stderr to the agent;
# the tool call itself has already run.
#
# agent/session.sh sets SCOUT_TURN_BUDGET=<limit>:<soft>:<hard>, for example 60:40:50.
#   at <soft> calls:   one reminder to finish the current step and save work
#   from <hard> calls: after every call, "stop now: commit, handoff, journal"
# Without SCOUT_TURN_BUDGET (smoke tests, the rules check) it does nothing.
#
# Tool calls approximate turns. Parallel calls in one turn count separately, so the warnings
# come early rather than late. The counter lives in the container's /tmp, which starts empty
# for every session. This is guidance for the agent, not a security control.

set -u
budget=${SCOUT_TURN_BUDGET:-}
[[ $budget =~ ^([0-9]+):([0-9]+):([0-9]+)$ ]] || exit 0
limit=${BASH_REMATCH[1]} soft=${BASH_REMATCH[2]} hard=${BASH_REMATCH[3]}

cat >/dev/null  # the tool call JSON isn't needed
file=${SCOUT_TURN_FILE:-/tmp/scout-tool-calls}
n=$(( $(cat "$file" 2>/dev/null || echo 0) + 1 ))
echo "$n" >"$file" 2>/dev/null

if ((n >= hard)); then
  echo "Scout turn budget: about $n of $limit turns used. STOP NOW: commit your work (git add, git commit), rewrite /workspace/notes/handoff.md, add one line to /workspace/notes/journal.md, then end the session. Do nothing else." >&2
  exit 2
fi
if ((n == soft)); then
  echo "Scout turn budget: about $n of $limit turns used. Finish the step you're on, commit it and update /workspace/notes/handoff.md. At about $hard turns you must stop." >&2
  exit 2
fi
exit 0
