"""Database access and the `scout db` commands (docs/phase1-plan.md task 1.1).

Connection settings come from the environment, so the same code runs in the box's
`scout` container, in the devcontainer and in tests:

  SCOUT_DB_HOST (default localhost), SCOUT_DB_PORT (5432), SCOUT_DB_NAME (scout)
  SCOUT_<ROLE>_PASSWORD for each role: MIGRATE, INGEST, AGENT, READER
  SCOUT_MIGRATIONS_DIR (default: migrations/ at the repo root)

Migrations are numbered SQL files (`0001_initial.sql`, ...). `migrate` applies the
missing ones in order, each in its own transaction, as scout_migrate, and records a
checksum. An applied file that has since been edited stops `migrate` with an error:
add a new file instead.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import errors

ROLES = ("migrate", "ingest", "agent", "reader")
SCHEMA = "scout"
EXPECTED_TABLES = (
    "sources",
    "employers",
    "scout_runs",
    "run_source_results",
    "postings",
    "posting_events",
)
EXPECTED_VIEWS = (
    "v_new_relevant_7d",
    "v_review",
    "v_suspicious",
    "v_source_health",
    "v_duplicates",
)
MIGRATION_RE = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class DbError(Exception):
    """A problem worth a one-line message rather than a traceback."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def migrations_dir() -> Path:
    return Path(os.environ.get("SCOUT_MIGRATIONS_DIR") or _repo_root() / "migrations")


def conninfo(role: str) -> str:
    if role not in ROLES:
        raise DbError(f"unknown role {role!r}; expected one of {', '.join(ROLES)}")
    var = f"SCOUT_{role.upper()}_PASSWORD"
    password = os.environ.get(var)
    if not password:
        raise DbError(f"{var} is not set (the scout container gets it from secrets/db-roles.env)")
    return psycopg.conninfo.make_conninfo(
        host=os.environ.get("SCOUT_DB_HOST", "localhost"),
        port=os.environ.get("SCOUT_DB_PORT", "5432"),
        dbname=os.environ.get("SCOUT_DB_NAME", "scout"),
        user=f"scout_{role}",
        password=password,
        application_name="scout",
        connect_timeout="10",
    )


def connect(role: str, **kwargs) -> psycopg.Connection:
    try:
        return psycopg.connect(conninfo(role), **kwargs)
    except psycopg.OperationalError as e:
        first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        raise DbError(f"can't connect as scout_{role}: {first}") from e


# One sweep or enrichment at a time, whether started by a timer or by hand. A
# session-level advisory lock: released when the connection closes, even after a crash.
RUN_LOCK_KEY = 5_0726_1031


def try_run_lock(conn: psycopg.Connection) -> bool:
    """Take the run lock on this connection; False if another run holds it."""
    return bool(conn.execute("SELECT pg_try_advisory_lock(%s)", (RUN_LOCK_KEY,)).fetchone()[0])


# --------------------------------------------------------------------------- migrations


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def find_migrations(folder: Path | None = None) -> list[Migration]:
    folder = folder or migrations_dir()
    if not folder.is_dir():
        raise DbError(f"migrations folder not found: {folder}")
    found: dict[str, Migration] = {}
    for path in sorted(folder.glob("*.sql")):
        m = MIGRATION_RE.match(path.name)
        if not m:
            raise DbError(f"bad migration file name {path.name!r} (expected NNNN_name.sql)")
        if m.group(1) in found:
            raise DbError(f"two migrations numbered {m.group(1)}")
        found[m.group(1)] = Migration(m.group(1), path.stem, path)
    return [found[v] for v in sorted(found)]


_LEDGER_SQL = f"""
CREATE TABLE IF NOT EXISTS {SCHEMA}.schema_migrations (
    version    text PRIMARY KEY,
    name       text NOT NULL,
    checksum   text NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""


def applied(conn: psycopg.Connection) -> dict[str, tuple[str, str]]:
    """version -> (name, checksum) for every applied migration."""
    rows = conn.execute(
        f"SELECT version, name, checksum FROM {SCHEMA}.schema_migrations ORDER BY version"
    ).fetchall()
    return {v: (n, c) for v, n, c in rows}


def _check_ledger(done: dict[str, tuple[str, str]], files: list[Migration]) -> None:
    by_version = {m.version: m for m in files}
    for version, (name, checksum) in done.items():
        m = by_version.get(version)
        if m is None:
            raise DbError(f"migration {version} ({name}) is applied but its file is missing")
        if m.checksum != checksum:
            raise DbError(
                f"{m.path.name} was edited after it was applied; "
                "put the change in a new migration file instead"
            )


def migrate(out=print) -> int:
    files = find_migrations()
    with connect("migrate", autocommit=True) as conn:
        conn.execute(_LEDGER_SQL)
        done = applied(conn)
        _check_ledger(done, files)
        pending = [m for m in files if m.version not in done]
        if not pending:
            out(f"up to date ({len(done)} applied)")
            return 0
        for m in pending:
            with conn.transaction():
                conn.execute(m.sql)
                conn.execute(
                    f"INSERT INTO {SCHEMA}.schema_migrations (version, name, checksum)"
                    " VALUES (%s, %s, %s)",
                    (m.version, m.name, m.checksum),
                )
            out(f"applied {m.path.name}")
    return 0


def status(out=print) -> int:
    files = find_migrations()
    with connect("migrate", autocommit=True) as conn:
        exists = conn.execute(
            "SELECT to_regclass(%s) IS NOT NULL", (f"{SCHEMA}.schema_migrations",)
        ).fetchone()[0]
        done = applied(conn) if exists else {}
    _check_ledger(done, files)
    for m in files:
        out(f"  {'applied' if m.version in done else 'PENDING'}  {m.path.name}")
    pending = sum(m.version not in done for m in files)
    out(f"{len(files) - pending} applied, {pending} pending")
    return 0


# --------------------------------------------------------------------------- check


class _Checker:
    def __init__(self, out):
        self.out = out
        self.passed = 0
        self.failed = 0

    def result(self, ok: bool, text: str) -> None:
        self.out(f"  {'PASS' if ok else 'FAIL'}  {text}")
        if ok:
            self.passed += 1
        else:
            self.failed += 1

    def attempt(self, conn: psycopg.Connection, sql: str, should_work: bool, text: str) -> None:
        """Run sql in a transaction that is always rolled back; record whether it was allowed."""
        try:
            with conn.transaction(force_rollback=True):
                conn.execute(sql)
            allowed = True
        except errors.InsufficientPrivilege:
            allowed = False
        except psycopg.Error as e:  # anything else is a real problem, not a permission result
            self.result(False, f"{text}: unexpected error: {type(e).__name__}: {e}".strip())
            return
        self.result(allowed == should_work, text)


# Each probe: (description, SQL, roles allowed to do it). Writes are always rolled back.
_PROBE_INSERT = "INSERT INTO sources (kind, name) VALUES ('workbc', '__scout_db_check__')"
_PROBES = [
    ("read postings", "SELECT count(*) FROM postings", set(ROLES)),
    ("read v_new_relevant_7d", "SELECT count(*) FROM v_new_relevant_7d", set(ROLES)),
    ("insert into sources", _PROBE_INSERT, {"migrate", "ingest", "agent"}),
    (
        "update postings",
        "UPDATE postings SET missed_runs = missed_runs WHERE false",
        {"migrate", "ingest", "agent"},
    ),
    ("delete from postings", "DELETE FROM postings WHERE false", {"migrate"}),
    (
        "update posting_events (history is append-only)",
        "UPDATE posting_events SET kind = kind WHERE false",
        {"migrate"},
    ),
    ("create a table", "CREATE TABLE __scout_db_check__ (x int)", {"migrate"}),
    ("change a table", "ALTER TABLE postings ADD COLUMN __scout_db_check__ int", {"migrate"}),
    (
        "create a table in public",
        "CREATE TABLE public.__scout_db_check__ (x int)",
        set(),
    ),
]


def check(out=print) -> int:
    c = _Checker(out)
    files = find_migrations()
    for role in ROLES:
        out(f"\n== scout_{role}")
        try:
            conn = connect(role, autocommit=True)
        except DbError as e:
            c.result(False, str(e))
            continue
        with conn:
            version, superuser, path = conn.execute(
                "SELECT current_setting('server_version'), "
                "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user), "
                "current_setting('search_path')"
            ).fetchone()
            c.result(True, f"connected (PostgreSQL {version}, search_path {path})")
            c.result(not superuser, "not a superuser")

            if role == "migrate":
                exists = conn.execute(
                    "SELECT to_regclass(%s) IS NOT NULL", (f"{SCHEMA}.schema_migrations",)
                ).fetchone()[0]
                done = applied(conn) if exists else {}
                missing = [m.path.name for m in files if m.version not in done]
                c.result(
                    not missing,
                    "all migrations applied"
                    if not missing
                    else f"migrations not applied: {', '.join(missing)} (run `scout db migrate`)",
                )

            present = {
                name
                for (name,) in conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                    (SCHEMA,),
                )
            }
            missing = [t for t in EXPECTED_TABLES + EXPECTED_VIEWS if t not in present]
            c.result(
                not missing,
                "sees all tables and views" if not missing else f"can't see: {', '.join(missing)}",
            )
            if missing:
                continue  # the probes below would only repeat the same failure

            for text, sql, allowed_roles in _PROBES:
                should = role in allowed_roles
                c.attempt(conn, sql, should, f"{'can' if should else 'cannot'} {text}")

    out(f"\n{c.passed} passed, {c.failed} failed")
    return 0 if c.failed == 0 else 1
