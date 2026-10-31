"""Duplicate groups (docs/phase1-plan.md §3 step 8, task 1.7).

Postings that look like the same job share a group. Nothing is merged or deleted: each
posting keeps its own row, and `dup_group_id` (the smallest posting id in the group) ties
them together. Postings with no likely duplicate get no group.

Two postings are linked when any of these holds:

  link          the same canonical link (WorkBC listing one employer posting twice)
  title         the same normalized employer and the same normalized title, first seen
                within 30 days of each other
  requisition   the same requisition number in their links, from related employers
                (same first word of the normalized name): one Activision role appeared as
                "Activision Blizzard", "Activision Blizzard, Inc." and "Activision", on
                three sites, all carrying R028031

Links are transitive (A~B and B~C puts A, B and C in one group).
"""

from __future__ import annotations

import re
from collections import defaultdict
from itertools import pairwise
from dataclasses import dataclass
from datetime import datetime, timedelta

TITLE_WINDOW = timedelta(days=30)

# A requisition number: "r"/"jr"/"req" + 5 or more digits, possibly glued to other text
# (ACCOUSR028031EXTERNAL, Data-Scientist_R028031-1, JR-0110326, REQ-12345).
_REQ = re.compile(r"(?:req|jr|r)[-_]?(\d{5,})")


@dataclass(frozen=True)
class Item:
    id: int | str
    canonical_url: str | None
    employer_norm: str | None
    title: str
    first_seen: datetime
    jump_url: str | None


def norm_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", title.casefold()))


def requisitions(url: str | None) -> set[str]:
    if not url:
        return set()
    return set(_REQ.findall(url.casefold()))


def _family(employer_norm: str | None) -> str | None:
    return employer_norm.split()[0] if employer_norm else None


class _UnionFind:
    def __init__(self, ids):
        self.parent = {i: i for i in ids}

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb, key=_sort_key)] = min(ra, rb, key=_sort_key)


def _sort_key(i):
    return (0, i, "") if isinstance(i, int) else (1, 0, str(i))


def groups(items: list[Item]) -> dict[int | str, int | str]:
    """Posting id -> group id (the smallest id in its group), for postings in a group only."""
    uf = _UnionFind([it.id for it in items])

    def link_all(buckets: dict) -> None:
        for members in buckets.values():
            for other in members[1:]:
                uf.union(members[0], other)

    by_url: dict[str, list] = defaultdict(list)
    by_req: dict[tuple[str, str], list] = defaultdict(list)
    by_title: dict[tuple[str, str], list[Item]] = defaultdict(list)
    for it in items:
        if it.canonical_url:
            by_url[it.canonical_url].append(it.id)
        family = _family(it.employer_norm)
        if family:
            for req in requisitions(it.jump_url):
                by_req[(family, req)].append(it.id)
        if it.employer_norm and norm_title(it.title):
            by_title[(it.employer_norm, norm_title(it.title))].append(it)

    link_all(by_url)
    link_all(by_req)
    for members in by_title.values():
        members.sort(key=lambda it: it.first_seen)
        for a, b in pairwise(members):
            if b.first_seen - a.first_seen <= TITLE_WINDOW:
                uf.union(a.id, b.id)

    roots: dict = defaultdict(list)
    for it in items:
        roots[uf.find(it.id)].append(it.id)
    return {i: root for root, ids in roots.items() if len(ids) > 1 for i in ids}
