#!/usr/bin/env bash
# Creates the two database secret files, with random passwords, once:
#
#   $SCOUT_HOME/secrets/db-admin.env   POSTGRES_PASSWORD (superuser; only the db container)
#   $SCOUT_HOME/secrets/db-roles.env   passwords for scout_migrate / ingest / agent / reader
#
# Both are mode 600 and never in git. Refuses to overwrite existing files: the passwords
# are baked into the database the first time it starts (see "Starting over" in
# docs/box-setup.md to change them).

set -euo pipefail

SECRETS=${SCOUT_HOME:-/srv/scout}/secrets
ADMIN=$SECRETS/db-admin.env
ROLES=$SECRETS/db-roles.env

for f in "$ADMIN" "$ROLES"; do
  if [[ -e $f ]]; then
    echo "$f already exists; not overwriting." >&2
    exit 1
  fi
done

# 48 hex characters: no quoting problems in env files, SQL or connection strings.
pw() { python3 -c 'import secrets; print(secrets.token_hex(24))'; }

umask 077
printf 'POSTGRES_PASSWORD=%s\n' "$(pw)" > "$ADMIN"
{
  printf 'SCOUT_MIGRATE_PASSWORD=%s\n' "$(pw)"
  printf 'SCOUT_INGEST_PASSWORD=%s\n' "$(pw)"
  printf 'SCOUT_AGENT_PASSWORD=%s\n' "$(pw)"
  printf 'SCOUT_READER_PASSWORD=%s\n' "$(pw)"
} > "$ROLES"
chmod 600 "$ADMIN" "$ROLES"
echo "created $ADMIN and $ROLES (mode 600)"
