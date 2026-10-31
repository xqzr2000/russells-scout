"""Tests for the link classifier and employer names (task 1.5).

One case per row of docs/phase1-plan.md §3 step 5. URL shapes follow the live sweeps;
board names and IDs are made up.
"""

from __future__ import annotations

import pytest

from scout.pipeline.links import canonical_url, classify_link, normalize_employer


@pytest.mark.parametrize(
    ("url", "ats", "board", "posting_id"),
    [
        (
            "https://jobs.ashbyhq.com/northwind/11111111-2222-3333-4444-555555555555",
            "ashby", "northwind", "11111111-2222-3333-4444-555555555555",
        ),
        (
            "https://jobs.ashbyhq.com/northwind/11111111-2222/application?utm_source=workbc",
            "ashby", "northwind", "11111111-2222",
        ),
        ("https://job-boards.greenhouse.io/contoso/jobs/8525610002", "greenhouse", "contoso",
         "8525610002"),
        ("https://boards.greenhouse.io/contoso/jobs/123", "greenhouse", "contoso", "123"),
        ("https://job-boards.eu.greenhouse.io/contoso/jobs/9", "greenhouse", "contoso", "9"),
        ("https://boards.greenhouse.io/embed/job_app?for=contoso&token=77", "greenhouse",
         "contoso", "77"),
        ("https://www.example.com/jobs/apply/8275149?gh_jid=8275149", "greenhouse", None,
         "8275149"),
        ("https://jobs.lever.co/fabrikam/abcdef12-3456", "lever", "fabrikam", "abcdef12-3456"),
        ("https://jobs.lever.co/fabrikam/abcdef12-3456/apply", "lever", "fabrikam",
         "abcdef12-3456"),
        (
            (
                "https://example.wd5.myworkdayjobs.com/Example/job/Canada-BC-Vancouver/"
                "Sr-Product-Data-Scientist_JR-0110326"
            ),
            "workday", "example/Example", "JR-0110326",
        ),
        (
            (
                "https://games.wd1.myworkdayjobs.com/External/job/Sherman-Oaks/"
                "Data-Scientist--Anti-Cheat_R028031-1"
            ),
            "workday", "games/External", "R028031-1",
        ),
        (
            "https://example.wd3.myworkdayjobs.com/en-US/Careers/job/Vancouver/Analyst_R-1",
            "workday", "example/Careers", "R-1",
        ),
        ("https://jobs.smartrecruiters.com/Contoso/744000012345-data-analyst",
         "smartrecruiters", "Contoso", "744000012345"),
        ("https://apply.workable.com/northwind/j/ABC123/", "workable", "northwind", "ABC123"),
        (
            "https://jobs.workable.com/view/o4xfyBEJBHQ/hybrid-senior-data-scientist-in-vancouver",
            "workable", None, "o4xfyBEJBHQ",
        ),
        (
            (
                "https://fa-abcd-saasfaprod1.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/"
                "sites/CX_1/job/12345"
            ),
            "oracle", "fa-abcd-saasfaprod1", "12345",
        ),
        ("https://ats.rippling.com/northwind/jobs/0a1b2c", "rippling", "northwind", "0a1b2c"),
        ("https://www.comeet.com/jobs/contoso/12.34/data-engineer/AB.123", "comeet", "contoso",
         "AB.123"),
        ("https://careers.kula.ai/fabrikam/jobs/62748", "kula", "fabrikam", "62748"),
        ("https://jobs.gohire.io/northwind/data-analyst-123", "gohire", "northwind",
         "data-analyst-123"),
        ("https://jobs.example.com/job/ACCOUSR028031EXTERNAL/Data-Scientist", "custom", None,
         None),
        (None, "jobbank", None, None),
        ("", "jobbank", None, None),
    ],
)  # fmt: skip
def test_classify_link(url, ats, board, posting_id):
    info = classify_link(url)
    assert (info.ats, info.board, info.posting_id) == (ats, board, posting_id)


def test_host_drops_www():
    assert classify_link("https://www.amazon.example/jobs/1").host == "amazon.example"


def test_canonical_url_drops_tracking_keeps_ids():
    assert (
        canonical_url("HTTPS://Jobs.Lever.co/Fabrikam/abc/?utm_source=workbc&lever-via=x#top")
        == "https://jobs.lever.co/Fabrikam/abc"
    )
    assert (
        canonical_url("https://www.example.com/jobs/apply/8275149?gh_jid=8275149&utm_medium=x")
        == "https://www.example.com/jobs/apply/8275149?gh_jid=8275149"
    )
    a = "https://jobs.ashbyhq.com/n/1?utm_source=a"
    b = "https://jobs.ashbyhq.com/n/1/"
    assert canonical_url(a) == canonical_url(b)


@pytest.mark.parametrize(
    ("name", "norm"),
    [
        ("Activision Blizzard, Inc.", "activision blizzard"),
        ("Activision Blizzard", "activision blizzard"),
        ("Remarcable, Inc.", "remarcable"),
        ("Northwind Analytics Ltd.", "northwind analytics"),
        ("Contoso Corp", "contoso"),
        ("Fabrikam & Co. LLC", "fabrikam"),
        ("Smith & Wesson Data", "smith and wesson data"),  # "&" mid-name stays
        ("Co", "co"),  # never empty
        ("  ", None),
        (None, None),
    ],
)
def test_normalize_employer(name, norm):
    assert normalize_employer(name) == norm
