"""Tests for `scout sweep --dry-run`, replay, and posting records (tasks 1.3–1.5)."""

from __future__ import annotations

import json
from pathlib import Path

from scout import config, sweep
from scout.pipeline.record import build
from scout.sources import workbc

REPO = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "workbc"
PAGE = (FIXTURES / "page_mixed.json").read_bytes()
EMPTY = json.dumps({"result": [], "count": 0}).encode()


def fake_post(url, body):
    return PAGE if body["Keyword"] == "data scientist" else EMPTY


def dry_run(**kwargs):
    lines: list[str] = []
    code = sweep.run(True, out=lines.append, sleep=lambda s: None, **kwargs)
    return code, "\n".join(lines)


def test_records_from_fixture():
    cfg = config.load(REPO / "config" / "search.json")
    jobs = json.loads(PAGE)["result"]
    ds, jb, ml = (build(j, ["data scientist"], cfg) for j in jobs)

    assert (ds.area, ds.workplace, ds.match_quality) == ("metro", "unknown", "exact")
    assert (ds.ats, ds.ats_board, ds.employer_norm) == ("ashby", "northwind", "northwind analytics")
    assert ds.relevant and not ds.is_job_bank

    assert (jb.area, jb.workplace, jb.ats, jb.is_job_bank) == ("remote", "remote", "jobbank", True)
    assert (jb.hours, jb.period) == ("Full-time", "Permanent")
    assert jb.match_quality == "exact"  # "data engineer"

    assert (ml.area, ml.workplace, ml.ats, ml.ats_board) == ("remote", "remote", "lever", "contoso")
    assert "province_only_city" in ml.remote_signs


def test_dry_run_summary(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_RAW_DIR", str(tmp_path))
    monkeypatch.setenv("SCOUT_RAW_DIR_HOST", "/srv/scout/logs/workbc")
    code, text = dry_run(terms=["data scientist", "data analyst"], post=fake_post)
    assert code == 0, text
    assert "unique postings: 3" in text
    assert "metro 1" in text and "remote 2" in text
    assert "relevant (strong match, right place): 3" in text
    assert "Job Bank postings (no employer site): 1" in text
    assert "ashby boards (1): northwind" in text and "lever boards (1): contoso" in text
    assert "raw responses: /srv/scout/logs/workbc/sweep-" in text  # the box path
    assert "complete sweep: yes" in text
    assert len(list(tmp_path.glob("sweep-*/data_scientist-p1.json"))) == 1


def test_replay_uses_saved_pages_without_network(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_RAW_DIR", str(tmp_path))
    saved = tmp_path / "sweep-20261031-000000Z"
    saved.mkdir()
    (saved / "data_scientist-p1.json").write_bytes(PAGE)
    (saved / "data_analyst-p1.json").write_bytes(EMPTY)

    def no_network(url, body):
        raise AssertionError("replay must not call WorkBC")

    code, text = dry_run(
        terms=["data scientist", "data analyst"], post=no_network, replay=saved.name
    )
    assert code == 0, text
    assert "replaying" in text and "unique postings: 3" in text
    assert len(list(tmp_path.glob("sweep-*"))) == 1  # replay saves nothing new


def test_replay_reports_missing_pages(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_RAW_DIR", str(tmp_path))
    (tmp_path / "old").mkdir()
    code, text = dry_run(terms=["data scientist"], replay="old")
    assert code == 1
    assert "data_scientist-p1.json not in this sweep" in text


def test_replay_of_missing_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("SCOUT_RAW_DIR", str(tmp_path))
    code, text = dry_run(replay="sweep-nope")
    assert code == 1 and "no saved sweep at" in text


def test_replay_cannot_write_to_the_database():
    lines: list[str] = []
    assert sweep.run(False, replay="sweep-x", out=lines.append) == 2
    assert "--replay only works with --dry-run" in lines[0]


def test_save_without_database_settings_fails_before_contacting_workbc(monkeypatch):
    monkeypatch.delenv("SCOUT_INGEST_PASSWORD", raising=False)

    def no_network(url, body):
        raise AssertionError("must not contact WorkBC when the database is unreachable")

    lines: list[str] = []
    assert sweep.run(False, out=lines.append, post=no_network) == 1
    assert "SCOUT_INGEST_PASSWORD is not set" in lines[0]


def test_unknown_term_is_refused():
    code, text = dry_run(terms=["astronaut"])
    assert code == 1 and "not in search.json: astronaut" in text


def test_slug_matches_saved_file_names():
    assert workbc.slug("AI engineer") == "ai_engineer"
    assert workbc.slug("business data analyst") == "business_data_analyst"


def test_prune_raw_deletes_only_old_sweep_folders(tmp_path):
    from datetime import UTC, datetime

    for name in ("sweep-20260901-064000Z", "sweep-20261020-064000Z", "sweep-garbled", "notes"):
        (tmp_path / name).mkdir()
    (tmp_path / "sweep-20260801-064000Z").write_text("a file, not a folder")
    now = datetime(2026, 10, 31, 12, tzinfo=UTC)
    assert sweep.prune_raw(tmp_path, now=now) == 1
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["notes", "sweep-20260801-064000Z", "sweep-20261020-064000Z", "sweep-garbled"]
    assert sweep.prune_raw(tmp_path, now=now) == 0
