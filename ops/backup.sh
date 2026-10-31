#!/usr/bin/env bash
# Database backup: run every night by scout-backup.timer, and safe to run by hand.
#
#   cd /srv/scout/code && ops/backup.sh
#
# 1. pg_dump inside the db container  ->  $SCOUT_HOME/backups/scout-<time>.dump
# 2. Restore check: load the dump into a scratch database, compare row counts with the
#    live database, drop the scratch database. A dump that doesn't restore is a failure.
# 3. Keep the newest 14 dumps plus one dump from each of the last 8 Sundays; delete the
#    rest. Only after a backup that passed its check.
# 4. Write $SCOUT_HOME/backups/status (key=value lines; the daily digest reads it).
#
# Exit code 0 only if the dump and its restore check both worked.
#
#   ops/backup.sh --prune-only    step 3 alone (used by the tests)
#
# Settings (environment): SCOUT_HOME (/srv/scout), SCOUT_BACKUP_DIR ($SCOUT_HOME/backups),
# SCOUT_KEEP_DAILY (14), SCOUT_KEEP_WEEKLY (8). For the tests only: SCOUT_BACKUP_DIRECT=1
# runs the Postgres tools directly instead of inside the db container.

set -euo pipefail
umask 027

SCOUT_HOME=${SCOUT_HOME:-/srv/scout}
BACKUPS=${SCOUT_BACKUP_DIR:-$SCOUT_HOME/backups}
KEEP_DAILY=${SCOUT_KEEP_DAILY:-14}
KEEP_WEEKLY=${SCOUT_KEEP_WEEKLY:-8}
SUPERUSER=${SCOUT_PG_SUPERUSER:-postgres}
CHECK_DB=scout_restore_check
NAME_RE='^scout-[0-9]{8}-[0-9]{6}\.dump$'

say() { echo "backup: $*"; }

# ---------------------------------------------------------------- keeping old dumps

prune() {
  local -a dumps
  mapfile -t dumps < <(find "$BACKUPS" -maxdepth 1 -type f -printf '%f\n' | grep -E "$NAME_RE" | sort -r)
  local i=0 sundays=0 last_sunday="" f day keep removed=0
  for f in "${dumps[@]}"; do
    day=${f:6:8}
    keep=no
    if ((i < KEEP_DAILY)); then keep=yes; fi
    if [[ $(date -d "$day" +%u) == 7 && $day != "$last_sunday" ]] && ((sundays < KEEP_WEEKLY)); then
      keep=yes
      last_sunday=$day
      sundays=$((sundays + 1))
    fi
    i=$((i + 1))
    if [[ $keep == no ]]; then
      rm -f -- "$BACKUPS/$f"
      removed=$((removed + 1))
    fi
  done
  # Leftovers from a run that was killed halfway.
  find "$BACKUPS" -maxdepth 1 -type f -name 'scout-*.dump.partial' -mmin +720 -delete
  say "kept $((${#dumps[@]} - removed)) dump(s), deleted $removed"
}

if [[ ${1:-} == --prune-only ]]; then
  prune
  exit 0
fi

# ---------------------------------------------------------------- database helpers

if [[ ${SCOUT_BACKUP_DIRECT:-} == 1 ]]; then
  dbx() { "$@"; }
else
  cd "$SCOUT_HOME/code"
  dbx() { docker compose --profile scout exec -T db "$@"; }
fi
sql() { dbx env PGOPTIONS='-c client_min_messages=warning' psql -U "$SUPERUSER" -d "$1" -XAtq -v ON_ERROR_STOP=1 -c "$2"; }

# "table=rows table=rows …" for every table in schema scout.
COUNTS="SELECT coalesce(string_agg(format('%s=%s', c.relname,
          (xpath('/row/n/text()', query_to_xml(format('SELECT count(*) AS n FROM scout.%I', c.relname),
                                               false, true, '')))[1]::text), ' ' ORDER BY c.relname), '')
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'scout' AND c.relkind = 'r'"
# Last run id, and runs still going (a crashed run counts for an hour at most).
ACTIVITY="SELECT coalesce(max(id), 0) || '/' ||
            count(*) FILTER (WHERE finished_at IS NULL AND started_at > now() - interval '1 hour')
          FROM scout.scout_runs"

# ---------------------------------------------------------------- status file

STARTED=$(date --iso-8601=seconds)
STAMP=$(date +%Y%m%d-%H%M%S)
FILE="scout-$STAMP.dump"

write_status() {  # result message [bytes]
  local tmp="$BACKUPS/.status.tmp"
  {
    echo "time=$STARTED"
    echo "result=$1"
    echo "file=$FILE"
    echo "bytes=${3:-0}"
    echo "message=$2"
  } > "$tmp"
  mv -f "$tmp" "$BACKUPS/status"
}

fail() {
  echo "backup: FAILED: $*" >&2
  rm -f -- "$BACKUPS/$FILE.partial"
  sql postgres "DROP DATABASE IF EXISTS $CHECK_DB" >/dev/null 2>&1 || true
  write_status failed "$*"
  exit 1
}

# ---------------------------------------------------------------- 1. dump

if [[ ! -d $BACKUPS ]]; then
  echo "backup: FAILED: $BACKUPS doesn't exist (box-setup §16 creates it)" >&2
  exit 1
fi

activity_before=$(sql scout "$ACTIVITY") || fail "can't reach the database"
say "dumping to $BACKUPS/$FILE"
dbx pg_dump -U "$SUPERUSER" -d scout -Fc > "$BACKUPS/$FILE.partial" || fail "pg_dump failed"
live_counts=$(sql scout "$COUNTS") || fail "can't count rows in the live database"
activity_after=$(sql scout "$ACTIVITY") || fail "can't reach the database"
mv -f -- "$BACKUPS/$FILE.partial" "$BACKUPS/$FILE"
BYTES=$(stat -c %s "$BACKUPS/$FILE")
say "dump done: $BYTES bytes"

# ---------------------------------------------------------------- 2. restore check

sql postgres "DROP DATABASE IF EXISTS $CHECK_DB" >/dev/null || fail "can't drop an old $CHECK_DB"
sql postgres "CREATE DATABASE $CHECK_DB" >/dev/null || fail "can't create $CHECK_DB"
if ! dbx pg_restore -U "$SUPERUSER" -d "$CHECK_DB" --exit-on-error < "$BACKUPS/$FILE"; then
  fail "the dump doesn't restore (pg_restore failed); $FILE is kept for a look"
fi
restored_counts=$(sql "$CHECK_DB" "$COUNTS") || fail "can't count rows in the restored copy"
sql postgres "DROP DATABASE $CHECK_DB" >/dev/null || fail "can't drop $CHECK_DB"

if [[ -z $restored_counts ]]; then
  fail "the restored copy has no scout tables"
fi
note="restore check ok"
if [[ $restored_counts != "$live_counts" ]]; then
  busy=${activity_after#*/}
  if [[ $activity_before != "$activity_after" || $busy != 0 ]]; then
    note="restore check ok (a sweep was running, so row counts were not compared)"
  else
    fail "row counts differ after restore: live [$live_counts] restored [$restored_counts]"
  fi
fi
say "$note: $restored_counts"

# ---------------------------------------------------------------- 3. keep / delete, 4. status

prune
write_status ok "$note" "$BYTES"
say "ok"
