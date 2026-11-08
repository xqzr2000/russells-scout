#!/usr/bin/env bash
# WSL2: copies Scout's backups, daily digests and the agent's outbox from the box (box-setup §18).
#
#   ~/scout-mirror/pull-from-box.sh
#
# Run by Windows Task Scheduler every 6 hours and at login. Uses the ssh alias scoutbox-sync
# (the restricted sync key). It never deletes anything here, so a wiped box can't wipe the
# copies. Each run is logged in ~/scout-mirror/sync.log.
#
# agent/ is the agent's outbox (Phase 3): agent.bundle, manifest.txt and notes/. You attach
# agent.bundle in chat for review. rsync runs without -l, so a symbolic link on the box is
# skipped, never copied: nothing in the mirror can point at a file on this PC.
#
# Settings (environment): SCOUT_MIRROR (~/scout-mirror), SCOUT_SYNC_HOST (scoutbox-sync).

set -uo pipefail

DEST=${SCOUT_MIRROR:-$HOME/scout-mirror}
HOST=${SCOUT_SYNC_HOST:-scoutbox-sync}
SSH=${SCOUT_SYNC_SSH:-ssh -o BatchMode=yes -o ConnectTimeout=20}

mkdir -p "$DEST/backups" "$DEST/digests" "$DEST/agent"
log=$DEST/sync.log
failed=0
echo "== $(date --iso-8601=seconds) pulling from $HOST" >> "$log"
for folder in backups digests agent; do
  if rsync -rt -e "$SSH" "$HOST:$folder/" "$DEST/$folder/" >> "$log" 2>&1; then
    result="$folder: ok"
  else
    result="$folder: FAILED (details in $log)"
    failed=1
  fi
  echo "$result" | tee -a "$log"
done
exit "$failed"
