#!/usr/bin/env bash
# WSL2: copies Scout's backups and daily digests from the box (box-setup §18).
#
#   ~/scout-mirror/pull-from-box.sh
#
# Run by Windows Task Scheduler every 6 hours and at login. Uses the ssh alias scoutbox-sync
# (the restricted sync key). It never deletes anything here, so a wiped box can't wipe the
# copies. Each run is logged in ~/scout-mirror/sync.log.
#
# Settings (environment): SCOUT_MIRROR (~/scout-mirror), SCOUT_SYNC_HOST (scoutbox-sync).

set -uo pipefail

DEST=${SCOUT_MIRROR:-$HOME/scout-mirror}
HOST=${SCOUT_SYNC_HOST:-scoutbox-sync}
SSH=${SCOUT_SYNC_SSH:-ssh -o BatchMode=yes -o ConnectTimeout=20}

mkdir -p "$DEST/backups" "$DEST/digests"
log=$DEST/sync.log
failed=0
echo "== $(date --iso-8601=seconds) pulling from $HOST" >> "$log"
for folder in backups digests; do
  if rsync -rt -e "$SSH" "$HOST:$folder/" "$DEST/$folder/" >> "$log" 2>&1; then
    result="$folder: ok"
  else
    result="$folder: FAILED (details in $log)"
    failed=1
  fi
  echo "$result" | tee -a "$log"
done
exit "$failed"
