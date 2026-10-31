"""Tests for ops/backup.sh (task 2.2).

The keep/delete rules always run. The full backup runs against a local Postgres with
SCOUT_TEST_DB=1 and SCOUT_TEST_SUPERUSER_PASSWORD set (the script needs the superuser to
dump, restore and drop the scratch database); on the box the same commands run inside the
db container.
"""

from __future__ import annotations

import os
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

from scout import db

SCRIPT = Path(__file__).resolve().parent.parent / "ops" / "backup.sh"


def run_script(backups: Path, *args: str, extra_env: dict | None = None):
    env = {**os.environ, "SCOUT_BACKUP_DIR": str(backups), **(extra_env or {})}
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def make_dumps(folder: Path, first: date, days: int) -> None:
    for n in range(days):
        d = first + timedelta(days=n)
        (folder / f"scout-{d:%Y%m%d}-033000.dump").write_text("x")


def dump_days(folder: Path) -> list[str]:
    return sorted(p.name[6:14] for p in folder.glob("scout-*.dump"))


def test_prune_keeps_14_newest_and_8_sundays(tmp_path):
    make_dumps(tmp_path, date(2026, 6, 1), 153)  # 2026-06-01 .. 2026-10-31, one a day
    (tmp_path / "notes.txt").write_text("not a dump")
    r = run_script(tmp_path, "--prune-only")
    assert r.returncode == 0, r.stderr
    kept = dump_days(tmp_path)
    newest = [f"{date(2026, 10, 31) - timedelta(days=n):%Y%m%d}" for n in range(14)]
    assert set(newest) <= set(kept)
    sundays = [d for d in kept if date(int(d[:4]), int(d[4:6]), int(d[6:])).isoweekday() == 7]
    assert len(sundays) == 8
    assert sundays[-1] == "20261025" and sundays[0] == "20260906"
    assert len(kept) == 14 + 8 - 2  # two of the Sundays are among the newest 14
    assert (tmp_path / "notes.txt").exists()
    assert "deleted 133" in r.stdout


def test_prune_with_few_dumps_deletes_nothing(tmp_path):
    make_dumps(tmp_path, date(2026, 10, 25), 3)
    r = run_script(tmp_path, "--prune-only")
    assert r.returncode == 0, r.stderr
    assert len(dump_days(tmp_path)) == 3


def test_prune_with_an_empty_folder(tmp_path):
    r = run_script(tmp_path, "--prune-only")
    assert r.returncode == 0, r.stderr
    assert "kept 0 dump(s), deleted 0" in r.stdout


def test_missing_backup_folder_fails_clearly(tmp_path):
    r = run_script(tmp_path / "nope", extra_env={"SCOUT_BACKUP_DIRECT": "1"})
    assert r.returncode == 1
    assert "doesn't exist" in r.stderr


needs_superuser = pytest.mark.skipif(
    os.environ.get("SCOUT_TEST_DB") != "1" or not os.environ.get("SCOUT_TEST_SUPERUSER_PASSWORD"),
    reason="set SCOUT_TEST_DB=1 and SCOUT_TEST_SUPERUSER_PASSWORD to run the full backup",
)


def pg_env() -> dict:
    return {
        "SCOUT_BACKUP_DIRECT": "1",
        "PGHOST": os.environ.get("SCOUT_DB_HOST", "localhost"),
        "PGPORT": os.environ.get("SCOUT_DB_PORT", "5432"),
        "PGPASSWORD": os.environ["SCOUT_TEST_SUPERUSER_PASSWORD"],
    }


def status_of(folder: Path) -> dict:
    lines = (folder / "status").read_text().splitlines()
    return dict(line.split("=", 1) for line in lines)


@pytest.fixture
def some_data():
    db.migrate(out=lambda _line: None)
    with db.connect("ingest", autocommit=True) as c:
        c.execute("INSERT INTO scout_runs (kind, finished_at, ok) VALUES ('sweep', now(), true)")
    yield
    with db.connect("migrate", autocommit=True) as c:
        c.execute(
            "TRUNCATE posting_events, run_source_results, postings, employers, scout_runs,"
            " sources RESTART IDENTITY"
        )


def no_scratch_database() -> bool:
    env = {**os.environ, **pg_env()}
    out = subprocess.run(
        ["psql", "-U", "postgres", "-d", "postgres", "-XAtc",
         "SELECT count(*) FROM pg_database WHERE datname = 'scout_restore_check'"],
        env=env, capture_output=True, text=True, check=True,
    )  # fmt: skip
    return out.stdout.strip() == "0"


@needs_superuser
def test_backup_dumps_and_restore_checks(tmp_path, some_data):
    r = run_script(tmp_path, extra_env=pg_env())
    assert r.returncode == 0, r.stdout + r.stderr
    dumps = list(tmp_path.glob("scout-*.dump"))
    assert len(dumps) == 1 and dumps[0].stat().st_size > 1000
    s = status_of(tmp_path)
    assert s["result"] == "ok" and s["file"] == dumps[0].name
    assert s["message"] == "restore check ok"
    assert int(s["bytes"]) == dumps[0].stat().st_size
    assert "scout_runs=1" in r.stdout
    assert not list(tmp_path.glob("*.partial"))
    assert no_scratch_database()


@needs_superuser
def test_dump_that_does_not_restore_is_a_failure(tmp_path, some_data):
    shims = tmp_path / "bin"
    shims.mkdir()
    fake = shims / "pg_restore"
    fake.write_text("#!/bin/sh\necho 'pg_restore: error: simulated' >&2\nexit 1\n")
    fake.chmod(0o755)
    backups = tmp_path / "backups"
    backups.mkdir()
    env = {**pg_env(), "PATH": f"{shims}:{os.environ['PATH']}"}
    r = run_script(backups, extra_env=env)
    assert r.returncode == 1
    s = status_of(backups)
    assert s["result"] == "failed" and "doesn't restore" in s["message"]
    assert len(list(backups.glob("scout-*.dump"))) == 1  # kept for a look
    assert no_scratch_database()


@needs_superuser
def test_unreachable_database_is_a_failure(tmp_path):
    env = {**pg_env(), "PGPORT": "1"}
    r = run_script(tmp_path, extra_env=env)
    assert r.returncode == 1
    assert status_of(tmp_path)["result"] == "failed"
    assert not list(tmp_path.glob("scout-*"))
