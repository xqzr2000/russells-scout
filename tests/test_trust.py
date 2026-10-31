"""Tests for trust scoring (task 1.9; acceptance test 12). Postings are made up."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from scout import config, db, store, trust
from scout.pipeline.record import build
from scout.sources import workbc
from scout.trust import TrustInput

REPO = Path(__file__).resolve().parents[1]
CFG = config.load(REPO / "config" / "search.json")


def inp(**kw):
    base = {
        "title": "Data Scientist",
        "employer_name": "Northwind Analytics",
        "employer_norm": "northwind analytics",
        "ats": "ashby",
        "ats_board": "northwind",
        "jump_url": "https://jobs.ashbyhq.com/northwind/1",
        "link_host": "jobs.ashbyhq.com",
        "is_job_bank": False,
        "found_via": "workbc",
        "enriched": True,
    }
    base.update(kw)
    return TrustInput(**base)


def test_genuine_board_posting_is_trusted():
    value, reasons = trust.score(inp())
    assert value == 3 and reasons == ["+2 ats_board:ashby", "+1 confirmed_on_board"]


def test_job_bank_posting():
    value, reasons = trust.score(
        inp(ats="jobbank", ats_board=None, jump_url=None, link_host=None, enriched=False,
            is_job_bank=True)
    )  # fmt: skip
    assert (value, reasons) == (2, ["+2 job_bank"])


@pytest.mark.parametrize(
    ("host", "employer", "ok"),
    [
        ("www.amazon.jobs", "amazon", True),
        ("jobs.ea.com", "electronic arts", True),
        ("careers.lululemon.com", "lululemon athletica", True),
        ("cclfg.cclgroup.com", "connor clark and lunn financial group", True),
        ("www.metacareers.com", "meta", True),
        ("jobs.example-recruiting.com", "northwind analytics", False),
    ],
)
def test_employer_domain(host, employer, ok):
    _, reasons = trust.score(
        inp(ats="custom", ats_board=None, link_host=host, employer_norm=employer, enriched=False)
    )
    assert ("+2 employer_domain" in reasons) is ok


@pytest.mark.parametrize(
    ("board", "employer", "unrelated"),
    [
        ("wmg", "warner music group", False),
        ("ScribdInc", "scribd", False),
        ("gomotive", "motive", False),
        ("arcteryx.com", "arc teryx", False),
        ("matchgroup", "match group", False),
        ("internshiplist2000", "northwind analytics", True),
    ],
)
def test_board_name(board, employer, unrelated):
    _, reasons = trust.score(inp(ats="greenhouse", ats_board=board, employer_norm=employer))
    assert ("-1 board_name_unrelated" in reasons) is unrelated


def test_oracle_tenant_codes_are_not_unrelated_boards():
    _, reasons = trust.score(inp(ats="oracle", ats_board="fa-eomj-saasfaprod1"))
    assert "-1 board_name_unrelated" not in reasons


# Acceptance test 12: seeded scam postings are flagged with the right reasons, and kept.
SCAMS = {
    "asks_for_fee": "Work from home data entry. A refundable training fee of $99 is required.",
    "asks_for_sin_or_bank": "Send your SIN and a void cheque so we can set up payroll today.",
    "money_mule_duties": "Duties: receive and reship packages, and process bitcoin transfers.",
    "chat_app_contact": "No interview needed. Message our recruiter on Telegram to start.",
    "free_email_contact": "Apply now: hiring.manager.dataco@gmail.com",
}


@pytest.mark.parametrize(("flag", "description"), SCAMS.items())
def test_red_flags(flag, description):
    value, reasons = trust.score(inp(description=description))
    assert any(r.endswith(flag) for r in reasons)
    if flag != "free_email_contact":
        assert value < 0  # one serious flag outweighs a known job board


@pytest.mark.parametrize(
    "text",
    ["Experience building Telegram bots is a plus.", "Our app integrates with WhatsApp Business."],
)
def test_chat_app_mentioned_as_tech_is_not_contact(text):
    _, reasons = trust.score(inp(description=text))
    assert not any("chat_app" in r for r in reasons)


def test_sin_alone_needs_capitals():
    _, reasons = trust.score(
        inp(description="We sin- gle out great analysts. Using a single model.")
    )
    assert not any("sin_or_bank" in r for r in reasons)


@pytest.mark.parametrize(
    ("salary", "pay_max", "period", "flag"),
    [
        ("$190,000 to $210,000 annually", None, None, False),
        ("$45.00 hourly", None, None, False),
        ("$300 hourly", None, None, True),
        ("$950,000 annually", None, None, True),
        (None, 650000, "year", True),
        (None, 180000, "year", False),
    ],
)
def test_pay_far_above_normal(salary, pay_max, period, flag):
    _, reasons = trust.score(inp(salary_text=salary, pay_max=pay_max, pay_period=period))
    assert ("-2 pay_far_above_normal" in reasons) is flag


def test_no_employer_name():
    value, reasons = trust.score(inp(employer_name=None, employer_norm=None, ats="custom",
                                     ats_board=None, enriched=False))  # fmt: skip
    assert reasons == ["-2 no_employer_name"] and value == -2


# --------------------------------------------------------------------------- database

needs_db = pytest.mark.skipif(
    os.environ.get("SCOUT_TEST_DB") != "1", reason="set SCOUT_TEST_DB=1 to run database tests"
)


@pytest.fixture
def conn():
    db.migrate(out=lambda _line: None)
    with db.connect("ingest") as c, c.transaction(force_rollback=True):
        yield c


@needs_db
def test_scam_is_flagged_and_kept_in_the_database(conn):
    jobs = [
        {"JobId": "s1", "Title": "Data Analyst (work from home)", "City": "Vancouver",
         "EmployerName": "Quick Data Jobs", "SalarySummary": "$95 hourly",
         "ExternalSource": {"Source": [{"Source": "x", "Url":
             "https://quickdatajobs.example/apply?contact=whatsapp"}]}},
        {"JobId": "s2", "Title": "Data Analyst", "City": "Vancouver", "EmployerName": "Northwind",
         "ExternalSource": {"Source": [{"Source": "x", "Url": "https://jobs.lever.co/northwind/1"}]}},
    ]  # fmt: skip

    def post(url, body):
        found = jobs if body["Keyword"] == "data analyst" else []
        return json.dumps({"result": found, "count": len(found)}).encode()

    result = workbc.sweep(["data analyst"], page_size=50, max_pages=1, delay=0.5, post=post,
                          sleep=lambda s: None)  # fmt: skip
    records = [build(j, result.matched_terms[i], CFG) for i, j in result.jobs.items()]
    s = store.save_sweep(conn, result, records, CFG, all_terms=False)
    assert s.suspicious == 1
    rows = dict(conn.execute("SELECT title, trust_reasons FROM v_suspicious").fetchall())
    assert rows == {"Data Analyst (work from home)": ["+2 employer_domain", "-4 chat_app_contact"]}
    status = conn.execute("SELECT status FROM postings WHERE workbc_job_id = 's1'").fetchone()
    assert status == ("active",)  # flagged, kept
    good = conn.execute("SELECT trust_score FROM postings WHERE workbc_job_id = 's2'").fetchone()
    assert good == (2,)
