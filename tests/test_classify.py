"""Tests for area, workplace and title-match rules (task 1.4).

Cases mirror what the live sweeps showed (docs/phase1-plan.md §1), with made-up data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scout.pipeline import classify

REPO = Path(__file__).resolve().parents[1]
CFG = json.loads((REPO / "config" / "search.json").read_text())
METRO = CFG["area"]["metro"]
TERMS = CFG["terms"]


def job(city=None, title="Data Scientist", url=None, workplace_id=None):
    j = {"JobId": "x", "Title": title}
    if city is not None:
        j["City"] = city
    if url:
        j["ExternalSource"] = {"Source": [{"Source": "host", "Url": url}]}
    if workplace_id is not None:
        j["WorkplaceType"] = {"Id": workplace_id, "Description": "?"}
    return j


@pytest.mark.parametrize(
    ("posting", "expected"),
    [
        # every city seen in the live sweeps
        (job("Vancouver"), "metro"),
        (job("Burnaby"), "metro"),
        (job("Surrey"), "metro"),
        (job("Township of Langley"), "metro"),
        (job("Langley"), "metro"),
        (job("Coquitlam"), "metro"),
        (job("Richmond"), "metro"),
        (job("West Vancouver"), "metro"),
        (job("New Westminster"), "metro"),
        (job("North Vancouver"), "metro"),
        (job("City of North Vancouver"), "metro"),
        (job("District of North Vancouver"), "metro"),
        (job("vancouver, BC"), "metro"),
        (job("Victoria"), "other_bc"),
        (job("Kelowna"), "other_bc"),
        (job("British Columbia"), "remote"),  # WorkBC's fallback for no city
        (job("B.C."), "remote"),
        (job("Virtual job based in Toronto, ON", workplace_id=15141), "remote"),
        (job("Virtual job based in Mississauga, ON"), "remote"),
        (job("Toronto, ON"), "outside"),
        (job("Calgary, Alberta"), "outside"),
        (job("Victoria", title="ML Engineer (Remote - CAN)"), "remote"),
        (job("Kelowna", url="https://jobs.example.com/remote-data-analyst"), "remote"),
        (job("Vancouver", title="ML Engineer (Remote - CAN)"), "metro"),  # metro wins
        (job(None), "unknown"),
        (job(""), "unknown"),
        (job("Victoria", title="Remoteness Analyst"), "other_bc"),  # whole word only
    ],
)
def test_area(posting, expected):
    assert classify.area(posting, METRO) == expected


@pytest.mark.parametrize(
    ("posting", "expected"),
    [
        (job("Vancouver"), "unknown"),
        (job("Vancouver", workplace_id=15141), "remote"),
        (job("Vancouver", workplace_id=100000), "hybrid"),
        (job("Vancouver", workplace_id=0), "onsite"),
        (job("Vancouver", workplace_id=100001), "onsite"),
        (job("Vancouver", title="Senior ML Engineer (Remote - CAN)"), "remote"),
        (
            job("Vancouver", url="https://jobs.workable.com/view/abc/hybrid-senior-data-scientist"),
            "hybrid",
        ),
        (job("British Columbia"), "remote"),
        (job("Virtual job based in Toronto, ON"), "remote"),
    ],
)
def test_workplace(posting, expected):
    assert classify.workplace(posting) == expected


@pytest.mark.parametrize(
    ("title", "quality", "terms_hit"),
    [
        ("Senior Data Scientist - Fraud Detection", "exact", {"data scientist"}),
        ("Staff Data Scientist, Applied ML", "exact", {"data scientist", "machine learning"}),
        ("Data Scientists (2 positions)", "exact", {"data scientist"}),
        ("Machine Learning Engineer", "exact", {"machine learning", "ML engineer"}),
        ("Sr. ML Engineer", "exact", {"ML engineer", "machine learning"}),
        ("AI Engineer", "exact", {"AI engineer", "artificial intelligence"}),
        ("Business Data Analyst II", "exact", {"business data analyst", "data analyst"}),
        ("Quantitative Analyst", "exact", {"quantitative analyst"}),
        ("Data Engineer", "exact", {"data engineer"}),
        # "AI" alone matches the term "artificial intelligence" (and "ML" "machine learning")
        ("Software Engineer, AI Platform", "exact", {"artificial intelligence"}),
        # loose: the words are there, not as the phrase
        ("Software Engineer, Data Platform", "loose", set()),
        ("Senior Data Engineering Manager", "loose", set()),
        ("Engineer, Data Platform", "loose", set()),
        ("Analyst, Business Data", "loose", set()),
        ("Mail Engineer", "loose", set()),  # "ai" inside a word doesn't count
    ],
)
def test_title_match(title, quality, terms_hit):
    m = classify.title_match(title, TERMS)
    assert m.quality == quality
    assert set(m.exact_terms) == terms_hit


def test_norm_city():
    assert classify.norm_city("  Township of   Langley ") == "langley"
    assert classify.norm_city("Vancouver, B.C.") == "vancouver"
    assert classify.norm_city(None) == ""
