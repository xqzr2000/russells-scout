#!/usr/bin/env bash
# Forced command for the WSL2 sync key (box-setup §18): the only thing that key can run.
#
# It allows one kind of request: an rsync *download* of one of two folders, read-only:
#   backups/   ->  /srv/scout/backups
#   digests/   ->  /srv/scout/logs/digests
# Everything else is refused: a shell, any other command or folder, any upload. The real
# work is done by rrsync -ro, rsync's own restricted mode. After a successful download it
# writes the time to /srv/scout/logs/sync/last-sync, which the daily digest shows.
#
# Installed by ops/sync/add-sync-key.sh as
#   command="/srv/scout/code/ops/sync/sync-gate.sh",restrict ssh-ed25519 AAAA… scout-sync

set -uo pipefail

SCOUT_HOME=${SCOUT_HOME:-/srv/scout}
STAMP_DIR=${SCOUT_SYNC_STAMP_DIR:-$SCOUT_HOME/logs/sync}
RRSYNC=${SCOUT_RRSYNC:-rrsync}

refuse() {
  echo "scout-sync: refused: $1. This key can only download backups/ or digests/ with rsync." >&2
  exit 1
}

cmd=${SSH_ORIGINAL_COMMAND:-}
[[ -n $cmd ]] || refuse "no shell for this key"
read -r -a words <<< "$cmd"
if [[ ${words[0]} != rsync || ${words[1]:-} != --server || ${words[2]:-} != --sender ]]; then
  refuse "not an rsync download"
fi
((${#words[@]} >= 5)) || refuse "incomplete rsync command"
[[ ${words[-2]} == . ]] || refuse "one folder at a time"
case ${words[-1]} in
  backups | backups/) root=$SCOUT_HOME/backups ;;
  digests | digests/) root=$SCOUT_HOME/logs/digests ;;
  *) refuse "unknown folder '${words[-1]}'" ;;
esac

words[-1]=./
SSH_ORIGINAL_COMMAND="${words[*]}" "$RRSYNC" -ro "$root"
rc=$?
if ((rc == 0)); then
  if mkdir -p "$STAMP_DIR" 2>/dev/null; then
    tmp=$STAMP_DIR/.last-sync.tmp
    if (umask 022 && date --iso-8601=seconds > "$tmp"); then
      mv -f "$tmp" "$STAMP_DIR/last-sync"
    fi
  fi
fi
exit "$rc"
