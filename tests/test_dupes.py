"""Tests for duplicate groups (task 1.7). URL shapes follow the live sweep; data made up."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from scout.pipeline import dupes
from scout.pipeline.dupes import Item

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def item(i, employer="northwind", title="Data Scientist", url=None, canon=None, days=0):
    url = url or f"https://jobs.example.com/{i}"
    return Item(i, canon or url, employer, title, T0 + timedelta(days=days), url)


def test_activision_case_one_role_three_names_three_sites():
    items = [
        item(1, "activision blizzard", "Data Scientist, Anti-Cheat",
             "https://careers.activisionblizzard.com/job/ACCOUSR028031EXTERNAL/Data-Scientist"),
        item(2, "activision blizzard", "Data Scientist, Anti-Cheat",
             "https://games.wd1.myworkdayjobs.com/External/job/Sherman-Oaks/Data-Scientist_R028031-1"),
        item(3, "activision", "Data Scientist, Anti-Cheat",
             "https://careers.activision.com/job/ACPUUSR028031EXTERNAL/Data-Scientist"),
        item(4, "activision", "Data Engineer",
             "https://careers.activision.com/job/ACPUUSR099999EXTERNAL/Data-Engineer"),
    ]  # fmt: skip
    g = dupes.groups(items)
    assert g == {1: 1, 2: 1, 3: 1}  # item 4 is a different role


def test_same_canonical_link_is_a_group():
    g = dupes.groups([item(1, canon="https://a/1"), item(2, "other", "X", canon="https://a/1")])
    assert g == {1: 1, 2: 1}


def test_same_employer_and_title_within_30_days():
    g = dupes.groups([item(1, title="Senior Data Scientist"),
                      item(2, title="Senior  data-scientist", days=29)])  # fmt: skip
    assert g == {1: 1, 2: 1}


def test_same_title_more_than_30_days_apart_is_not_a_group():
    assert dupes.groups([item(1), item(2, days=31)]) == {}


def test_same_title_different_employer_is_not_a_group():
    assert dupes.groups([item(1, "northwind"), item(2, "contoso")]) == {}


def test_shared_requisition_needs_related_employers():
    a = item(1, "northwind", url="https://x.wd1.myworkdayjobs.com/E/job/V/Analyst_R123456")
    b = item(2, "contoso", "Other", url="https://y.wd5.myworkdayjobs.com/E/job/V/Other_R123456")
    assert dupes.groups([a, b]) == {}


def test_links_are_transitive_and_group_id_is_smallest():
    items = [item(5, canon="https://a/1"), item(3, canon="https://a/1"),
             item(9, url="https://a/2", title="Data Scientist", days=10)]  # fmt: skip
    # 5~3 by link; 9~(3,5) by employer + title
    assert dupes.groups(items) == {3: 3, 5: 3, 9: 3}


def test_requisition_extraction():
    assert dupes.requisitions("https://x/job/ACCOUSR028031EXTERNAL/a") == {"028031"}
    assert dupes.requisitions("https://x/Sr-Product-Data-Scientist_JR-0110326") == {"0110326"}
    assert dupes.requisitions("https://jobs.ashbyhq.com/remarcable-inc/51302674-25d6") == set()
    assert dupes.requisitions(None) == set()


def test_string_ids_for_dry_run():
    g = dupes.groups([item("b", canon="https://a/1"), item("a", canon="https://a/1")])
    assert g == {"a": "a", "b": "a"}
