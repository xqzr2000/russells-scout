"""Area, workplace and title-match rules (docs/phase1-plan.md §3 steps 3–4, task 1.4).

All plain code, no LLM. Each function takes one raw WorkBC job (the dict from the search
response) and returns a label.

Area (where the job is, for relevance):
  metro     the city is a Metro Vancouver municipality (config area.metro)
  remote    not in Metro Vancouver, but remote: WorkBC's "Virtual job" flag, a city of
            "Virtual job based in …", a city given only as "British Columbia" (WorkBC's
            fallback when an external posting has no city), or "remote" in title or link
  other_bc  somewhere else in B.C.
  outside   a city with another province's code (e.g. "Toronto, ON") and no remote sign
  unknown   no city and no remote sign

A remote job in a Metro Vancouver city stays `metro`; its `workplace` says `remote`.

Workplace: remote / hybrid / onsite / unknown, from WorkBC's WorkplaceType (Job Bank
postings only) or "remote"/"hybrid" in the title or link.

Title match: `exact` when a search term appears as a whole phrase in the title
(case-insensitive, punctuation ignored, "ML" = "machine learning", "AI" = "artificial
intelligence", a trailing plural "s" allowed), otherwise `loose`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

# WorkBC WorkplaceType ids (bcgov/workbc-jb JobSearchQuery.cs)
WORKPLACE_ONSITE = 0
WORKPLACE_HYBRID = 100000
WORKPLACE_TRAVELLING = 100001
WORKPLACE_VIRTUAL = 15141

PROVINCE_ONLY = {"britishcolumbia", "bc"}  # compared with spaces and dots removed
OTHER_PROVINCES = {
    "ab", "sk", "mb", "on", "qc", "nb", "ns", "pe", "nl", "yt", "nt", "nu",
    "alberta", "saskatchewan", "manitoba", "ontario", "quebec", "québec", "new brunswick",
    "nova scotia", "prince edward island", "newfoundland and labrador", "yukon",
    "northwest territories", "nunavut",
}  # fmt: skip
CITY_PREFIXES = ("city of ", "district of ", "township of ", "town of ", "village of ")
SYNONYMS = {"ml": "machine learning", "ai": "artificial intelligence"}

_REMOTE_WORD = re.compile(r"(?<![a-z])remote(?![a-z])")
_HYBRID_WORD = re.compile(r"(?<![a-z])hybrid(?![a-z])")


def norm_city(city: str | None) -> str:
    """'Township of Langley' -> 'langley'; 'Vancouver, BC' -> 'vancouver'."""
    c = " ".join((city or "").casefold().split())
    c = re.sub(r",\s*(bc|b\.c\.|british columbia)$", "", c)
    c = next((c.removeprefix(p) for p in CITY_PREFIXES if c.startswith(p)), c)
    return c.strip(" ,")


def _workplace_id(job: dict) -> int | None:
    wt = job.get("WorkplaceType")
    if isinstance(wt, dict) and isinstance(wt.get("Id"), int):
        return wt["Id"]
    return None


def _link_text(job: dict) -> str:
    """Path and query of the jump link, lowercased (the host can't say 'remote')."""
    sources = (job.get("ExternalSource") or {}).get("Source") or []
    for s in sources:
        if isinstance(s, dict) and s.get("Url"):
            p = urlparse(s["Url"])
            return f"{p.path} {p.query}".casefold()
    return ""


def _says(pattern: re.Pattern, job: dict) -> bool:
    return bool(
        pattern.search((job.get("Title") or "").casefold()) or pattern.search(_link_text(job))
    )


def remote_signs(job: dict) -> list[str]:
    """Why a posting looks remote (empty list if it doesn't)."""
    signs = []
    if _workplace_id(job) == WORKPLACE_VIRTUAL:
        signs.append("workbc_virtual")
    city = " ".join((job.get("City") or "").casefold().split())
    if city.startswith("virtual job"):
        signs.append("virtual_city")
    if city and re.sub(r"[\s.,]", "", city) in PROVINCE_ONLY:
        signs.append("province_only_city")
    if _says(_REMOTE_WORD, job):
        signs.append("remote_in_title_or_link")
    return signs


def area(job: dict, metro: Iterable[str]) -> str:
    city = norm_city(job.get("City"))
    if city and not city.startswith("virtual job") and city in {norm_city(m) for m in metro}:
        return "metro"
    if remote_signs(job):
        return "remote"
    if not city:
        return "unknown"
    if "," in city and city.rsplit(",", 1)[1].strip(" .") in OTHER_PROVINCES:
        return "outside"
    return "other_bc"


def workplace(job: dict) -> str:
    wid = _workplace_id(job)
    if wid == WORKPLACE_VIRTUAL:
        return "remote"
    if wid == WORKPLACE_HYBRID:
        return "hybrid"
    if wid in (WORKPLACE_ONSITE, WORKPLACE_TRAVELLING):
        return "onsite"
    if _says(_REMOTE_WORD, job):
        return "remote"
    if _says(_HYBRID_WORD, job):
        return "hybrid"
    if remote_signs(job):  # virtual or province-only city
        return "remote"
    return "unknown"


def _words(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.casefold())
    out: list[str] = []
    for w in words:
        out.extend(SYNONYMS.get(w, w).split())
    return out


@dataclass(frozen=True)
class TitleMatch:
    quality: str  # exact | loose
    exact_terms: tuple[str, ...]


def title_match(title: str, terms: Iterable[str]) -> TitleMatch:
    title_words = " " + " ".join(_words(title)) + " "
    exact = []
    for term in terms:
        tw = _words(term)
        if not tw:
            continue
        phrase = " ".join(tw)
        # whole words only; allow a plural "s" on the last word ("Data Scientists")
        if f" {phrase} " in title_words or f" {phrase}s " in title_words:
            exact.append(term)
    return TitleMatch("exact" if exact else "loose", tuple(exact))


def term_hits(title: str, terms: Iterable[str]) -> tuple[str, ...]:
    """Terms whose words all appear in the title, in any order: what WorkBC's title search
    returns. Used to filter postings found directly on job boards the same way."""
    words = set(_words(title))
    hits = []
    for term in terms:
        tw = _words(term)
        if tw and all(w in words or f"{w}s" in words for w in tw):
            hits.append(term)
    return tuple(hits)
