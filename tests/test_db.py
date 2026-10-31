"""Tests for scout.db (task 1.1).

The unit tests need no database. The integration tests run only when SCOUT_TEST_DB=1 and
the SCOUT_DB_* / SCOUT_*_PASSWORD variables point at a database prepared with
ops/db/initdb/01-roles.sh (see ops/db/README.md). They never leave data behind: every
write happens in a transaction that is rolled back.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from scout import db

REPO = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- unit


def test_repo_migrations_are_well_formed():
    migrations = db.find_migrations(REPO / "migrations")
    assert migrations[0].version == "0001"
    assert all(m.sql.strip() for m in migrations)


@pytest.mark.parametrize("name", ["1_initial.sql", "0002-initial.sql", "0002_Initial.sql"])
def test_bad_migration_names_are_rejected(tmp_path, name):
    (tmp_path / name).write_text("SELECT 1;")
    with pytest.raises(db.DbError, match="bad migration file name"):
        db.find_migrations(tmp_path)


def test_duplicate_migration_numbers_are_rejected(tmp_path):
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    (tmp_path / "0001_b.sql").write_text("SELECT 1;")
    with pytest.raises(db.DbError, match="two migrations numbered 0001"):
        db.find_migrations(tmp_path)


def test_ledger_detects_edited_and_missing_files(tmp_path):
    (tmp_path / "0001_a.sql").write_text("SELECT 1;")
    files = db.find_migrations(tmp_path)
    good = {"0001": ("0001_a", files[0].checksum)}
    db._check_ledger(good, files)  # no error

    with pytest.raises(db.DbError, match="edited after it was applied"):
        db._check_ledger({"0001": ("0001_a", "0" * 64)}, files)
    with pytest.raises(db.DbError, match="its file is missing"):
        db._check_ledger({**good, "0002": ("0002_b", "x")}, files)


def test_missing_password_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("SCOUT_READER_PASSWORD", raising=False)
    with pytest.raises(db.DbError, match="SCOUT_READER_PASSWORD is not set"):
        db.conninfo("reader")


def test_unknown_role_is_rejected():
    with pytest.raises(db.DbError, match="unknown role"):
        db.conninfo("postgres")


# --------------------------------------------------------------------------- integration

needs_db = pytest.mark.skipif(
    os.environ.get("SCOUT_TEST_DB") != "1", reason="set SCOUT_TEST_DB=1 to run database tests"
)


@pytest.fixture(scope="module")
def migrated():
    out: list[str] = []
    assert db.migrate(out=out.append) == 0
    return out


@needs_db
def test_check_passes(migrated):
    lines: list[str] = []
    assert db.check(out=lines.append) == 0, "\n".join(lines)
    assert lines[-1].endswith(" 0 failed")


@needs_db
def test_migrate_is_idempotent(migrated):
    out: list[str] = []
    assert db.migrate(out=out.append) == 0
    assert out == [f"up to date ({len(db.find_migrations())} applied)"]


@needs_db
def test_check_leaves_no_rows_behind(migrated):
    db.check(out=lambda _line: None)
    with db.connect("reader") as conn:
        assert conn.execute("SELECT count(*) FROM sources").fetchone()[0] == 0


@needs_db
def test_ingest_can_record_a_posting_and_reader_sees_it(migrated):
    with db.connect("ingest") as conn, conn.transaction(force_rollback=True):
        src = conn.execute(
            "INSERT INTO sources (kind, name) VALUES ('workbc', 'workbc-test') RETURNING id"
        ).fetchone()[0]
        emp = conn.execute(
            "INSERT INTO employers (name, name_norm, ats, ats_board)"
            " VALUES ('Example Corp', 'example', 'ashby', 'example') RETURNING id"
        ).fetchone()[0]
        run = conn.execute(
            "INSERT INTO scout_runs (kind) VALUES ('sweep') RETURNING id"
        ).fetchone()[0]
        post = conn.execute(
            "INSERT INTO postings (workbc_job_id, found_via, source_id, title, employer_id,"
            " city, area, match_quality, matched_terms)"
            " VALUES ('t-1', 'workbc', %s, 'Data Scientist', %s, 'Vancouver', 'metro',"
            " 'exact', ARRAY['data scientist']) RETURNING id",
            (src, emp),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO posting_events (posting_id, run_id, kind) VALUES (%s, %s, 'new')",
            (post, run),
        )
        row = conn.execute(
            "SELECT title, employer FROM v_new_relevant_7d WHERE id = %s", (post,)
        ).fetchone()
        assert row == ("Data Scientist", "Example Corp")


@needs_db
def test_check_constraints_reject_bad_values(migrated):
    import psycopg

    with (
        db.connect("ingest") as conn,
        pytest.raises(psycopg.errors.CheckViolation),
        conn.transaction(force_rollback=True),
    ):
        conn.execute("INSERT INTO postings (found_via, title, area) VALUES ('workbc', 'x', 'mars')")


@needs_db
def test_edited_migration_stops_migrate(migrated, tmp_path, monkeypatch):
    for f in (REPO / "migrations").glob("*.sql"):
        shutil.copy(f, tmp_path / f.name)
    first = min(tmp_path.glob("*.sql"))
    first.write_text(first.read_text() + "\n-- edited\n")
    monkeypatch.setenv("SCOUT_MIGRATIONS_DIR", str(tmp_path))
    with pytest.raises(db.DbError, match="edited after it was applied"):
        db.migrate(out=lambda _line: None)
