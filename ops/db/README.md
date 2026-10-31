# Database setup (`ops/db/`)

| File | What it does |
|---|---|
| `make-secrets.sh` | Creates `secrets/db-admin.env` (superuser) and `secrets/db-roles.env` (the four `scout_*` roles) with random passwords, once |
| `initdb/01-roles.sh` | Runs inside the `db` container on its first start (empty data folder): creates the `scout` database, the `scout` schema and the four roles |

Tables, grants and views come from `migrations/*.sql`, applied with `scout db migrate`.
The steps on the box are in [docs/box-setup.md §12](../../docs/box-setup.md#12-database-phase-1-box-once).

## Testing without Docker

The database tests in `tests/test_db.py` run against any Postgres 15+ prepared with
`01-roles.sh`. For example, with a throwaway cluster (run as a non-root user that owns `$D`):

```bash
D=/tmp/scout-pg; PGBIN=/usr/lib/postgresql/16/bin
echo adminpw > $D.pw
$PGBIN/initdb -D $D -U postgres --pwfile=$D.pw --auth=scram-sha-256 -E UTF8
$PGBIN/pg_ctl -D $D -o "-p 55432 -k /tmp -c listen_addresses=127.0.0.1" -l $D.log start

export SCOUT_MIGRATE_PASSWORD=m SCOUT_INGEST_PASSWORD=i SCOUT_AGENT_PASSWORD=a SCOUT_READER_PASSWORD=r
POSTGRES_USER=postgres POSTGRES_DB=postgres PGHOST=127.0.0.1 PGPORT=55432 PGPASSWORD=adminpw \
  ops/db/initdb/01-roles.sh

SCOUT_DB_HOST=127.0.0.1 SCOUT_DB_PORT=55432 SCOUT_TEST_DB=1 python -m pytest tests/test_db.py
```

Without `SCOUT_TEST_DB=1` the database tests are skipped and only the unit tests run.
