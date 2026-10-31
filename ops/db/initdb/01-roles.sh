#!/usr/bin/env bash
# Creates Scout's database, schema and login roles. Runs once, inside the postgres
# container, when the data folder is empty (docker-entrypoint-initdb.d). Tables come
# later from migrations/ (`scout db migrate`, run by you as scout_migrate).
#
# Passwords come from the environment (secrets/db-roles.env, made by
# ops/db/make-secrets.sh). They are passed as psql variables, so the shell never pastes
# them into SQL text.
#
# Roles (architecture plan §8):
#   scout_migrate  owns the schema; the only role that can change tables (human only)
#   scout_ingest   the scout runtime: reads and writes data, no schema changes
#   scout_agent    the agent (Phase 3): same data rights as ingest, no schema changes
#   scout_reader   read-only (you, through an SSH tunnel)

set -euo pipefail

for v in SCOUT_MIGRATE_PASSWORD SCOUT_INGEST_PASSWORD SCOUT_AGENT_PASSWORD SCOUT_READER_PASSWORD; do
  if [[ -z "${!v:-}" ]]; then
    echo "01-roles.sh: $v is not set (is secrets/db-roles.env missing?)" >&2
    exit 1
  fi
done

DB=${SCOUT_DB_NAME:-scout}

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v migrate_pw="$SCOUT_MIGRATE_PASSWORD" \
  -v ingest_pw="$SCOUT_INGEST_PASSWORD" \
  -v agent_pw="$SCOUT_AGENT_PASSWORD" \
  -v reader_pw="$SCOUT_READER_PASSWORD" \
  -v db="$DB" <<'SQL'
CREATE ROLE scout_migrate LOGIN PASSWORD :'migrate_pw';
CREATE ROLE scout_ingest  LOGIN PASSWORD :'ingest_pw';
CREATE ROLE scout_agent   LOGIN PASSWORD :'agent_pw';
CREATE ROLE scout_reader  LOGIN PASSWORD :'reader_pw';

-- Every role works in the scout schema by default.
ALTER ROLE scout_migrate SET search_path = scout;
ALTER ROLE scout_ingest  SET search_path = scout;
ALTER ROLE scout_agent   SET search_path = scout;
ALTER ROLE scout_reader  SET search_path = scout;

-- Guard rails against runaway queries from the runtime or the agent.
ALTER ROLE scout_ingest SET statement_timeout = '60s';
ALTER ROLE scout_agent  SET statement_timeout = '60s';
ALTER ROLE scout_reader SET statement_timeout = '120s';

CREATE DATABASE :"db" OWNER scout_migrate;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO scout_migrate, scout_ingest, scout_agent, scout_reader;
GRANT CREATE, TEMPORARY ON DATABASE :"db" TO scout_migrate;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$DB" <<'SQL'
-- Nobody creates objects in public; Scout lives in its own schema owned by scout_migrate.
-- (Since PostgreSQL 15 the database owner also owns public, so hand it back to the superuser.)
ALTER SCHEMA public OWNER TO CURRENT_USER;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
CREATE SCHEMA scout AUTHORIZATION scout_migrate;
GRANT USAGE ON SCHEMA scout TO scout_ingest, scout_agent, scout_reader;
SQL

echo "01-roles.sh: database '$DB', schema 'scout' and 4 roles created"
