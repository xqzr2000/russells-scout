"""Tests for config/search.json loading (task 1.2)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scout import config

REPO = Path(__file__).resolve().parents[1]
GOOD = json.loads((REPO / "config" / "search.json").read_text())


def parsed(change=None):
    raw = copy.deepcopy(GOOD)
    if change:
        change(raw)
    return config.parse(raw)


def test_repo_config_loads():
    cfg = config.load(REPO / "config" / "search.json")
    assert len(cfg.terms) == 10
    assert "data scientist" in cfg.terms
    assert "Vancouver" in cfg.area.metro
    assert cfg.area.include_remote and cfg.area.keep_other_bc
    assert cfg.sweep.page_size == 50 and cfg.sweep.delay_seconds == 1.0


def test_env_var_points_to_config(tmp_path, monkeypatch):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(GOOD))
    monkeypatch.setenv("SCOUT_CONFIG", str(p))
    assert config.load().path == p


def test_whitespace_in_terms_is_tidied():
    cfg = parsed(lambda r: r.update(terms=["  data   scientist "]))
    assert cfg.terms == ("data scientist",)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda r: r.update(term=["x"]), r"unknown key\(s\): term"),
        (lambda r: r.pop("sweep"), "is missing: sweep"),
        (lambda r: r.update(terms=[]), "terms must be a non-empty list"),
        (lambda r: r.update(terms=["a", ""]), r"terms\[1\] must be a non-empty string"),
        (lambda r: r.update(terms=["Data Scientist", "data scientist"]), "repeats"),
        (lambda r: r.update(terms=["x" * 101]), "longer than 100"),
        (lambda r: r["area"].update(include_remote="yes"), "must be true or false"),
        (lambda r: r["sweep"].update(page_size=500), "from 1 to 100"),
        (lambda r: r["sweep"].update(page_size=True), "from 1 to 100"),
        (lambda r: r["sweep"].update(max_pages_per_term=0), "from 1 to 50"),
        (lambda r: r["sweep"].update(delay_seconds=0.1), "≥ 0.5"),
        (lambda r: r["area"].update(metro="Vancouver"), "area.metro must be a non-empty list"),
    ],
)
def test_bad_values_are_named(change, message):
    with pytest.raises(config.ConfigError, match=message):
        parsed(change)


def test_missing_and_broken_files(tmp_path):
    with pytest.raises(config.ConfigError, match="file not found"):
        config.load(tmp_path / "nope.json")
    bad = tmp_path / "bad.json"
    bad.write_text('{"terms": [}')
    with pytest.raises(config.ConfigError, match="not valid JSON"):
        config.load(bad)
