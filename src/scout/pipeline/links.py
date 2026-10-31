"""Link classifier and employer names (docs/phase1-plan.md §3 steps 5–6, task 1.5).

`classify_link(url)` reads the job-board system (ATS), board name and posting ID from a
posting's jump link, and makes a canonical form of the link for duplicate checks.
`normalize_employer(name)` makes the key used to merge employer name variants.

ATS values: ashby, greenhouse, lever, workday, smartrecruiters, workable, oracle, rippling,
comeet, kula, gohire, custom (anything else), jobbank (no link).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

# Query parameters that identify a posting; every other parameter is tracking and dropped.
KEEP_QUERY = {"gh_jid", "jid", "jobid", "job_id", "id", "for", "token"}


@dataclass(frozen=True)
class LinkInfo:
    ats: str
    board: str | None = None
    posting_id: str | None = None
    host: str | None = None
    canonical_url: str | None = None


def canonical_url(url: str) -> str:
    p = urlparse(url.strip())
    query = parse_qs(p.query, keep_blank_values=False)
    kept = {k: v[0] for k, v in sorted(query.items()) if k.lower() in KEEP_QUERY and v}
    path = re.sub(r"/{2,}", "/", p.path).rstrip("/") or "/"
    return urlunparse(("https", p.netloc.lower(), path, "", urlencode(kept), ""))


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def classify_link(url: str | None) -> LinkInfo:
    if not url or not url.strip():
        return LinkInfo("jobbank")
    p = urlparse(url.strip())
    host = p.netloc.lower().split(":")[0]
    host = host.removeprefix("www.")
    seg = _segments(p.path)
    query = {k.lower(): v[0] for k, v in parse_qs(p.query).items() if v}
    canon = canonical_url(url)

    def info(ats, board=None, posting_id=None):
        return LinkInfo(ats, board, posting_id, host, canon)

    # Ashby: jobs.ashbyhq.com/{board}/{id}[/application]
    if host == "jobs.ashbyhq.com" and seg:
        return info("ashby", seg[0], seg[1] if len(seg) > 1 else None)

    # Greenhouse: job-boards.greenhouse.io/{board}/jobs/{id}, boards.greenhouse.io/…,
    # EU variants, the embed form ?for={board}&token={id}, and company sites with ?gh_jid=
    if host.endswith("greenhouse.io"):
        if len(seg) >= 3 and seg[1] == "jobs":
            return info("greenhouse", seg[0], seg[2])
        if "for" in query:
            return info("greenhouse", query["for"], query.get("token"))
        return info("greenhouse", seg[0] if seg else None)
    if "gh_jid" in query:
        return info("greenhouse", None, query["gh_jid"])

    # Lever: jobs.lever.co/{board}/{id}[/apply], jobs.eu.lever.co
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and seg:
        return info("lever", seg[0], seg[1] if len(seg) > 1 else None)

    # Workday: {tenant}.wd{n}.myworkdayjobs.com/[{locale}/]{site}/job/{location}/{slug}_{req}
    m = re.fullmatch(r"([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com", host)
    if m:
        tenant = m.group(1)
        rest = seg[1:] if seg and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", seg[0]) else seg
        site = rest[0] if rest else None
        req = None
        if "job" in rest and rest[-1] != "job":
            req = rest[-1].rsplit("_", 1)[1] if "_" in rest[-1] else None
        return info("workday", f"{tenant}/{site}" if site else tenant, req)

    # SmartRecruiters: jobs.smartrecruiters.com/{board}/{id}
    if host == "jobs.smartrecruiters.com" and seg:
        return info("smartrecruiters", seg[0], seg[1].split("-")[0] if len(seg) > 1 else None)

    # Workable: apply.workable.com/{board}/j/{id}; jobs.workable.com/view/{id}/… (no board)
    if host == "apply.workable.com" and seg:
        posting = seg[2] if len(seg) > 2 and seg[1] == "j" else None
        return info("workable", seg[0], posting)
    if host == "jobs.workable.com":
        return info("workable", None, seg[1] if len(seg) > 1 and seg[0] == "view" else None)

    # Oracle Recruiting Cloud: …oraclecloud.com/…/job/{id}
    if host.endswith(".oraclecloud.com"):
        posting = None
        if "job" in seg and seg.index("job") + 1 < len(seg):
            posting = seg[seg.index("job") + 1]
        return info("oracle", host.split(".")[0], posting)

    # Rippling: ats.rippling.com/{board}/jobs/{id}
    if host == "ats.rippling.com" and seg:
        posting = seg[2] if len(seg) > 2 and seg[1] == "jobs" else None
        return info("rippling", seg[0], posting)

    # Comeet: comeet.com/jobs/{board}/{company_uid}/{slug}/{id}
    if host == "comeet.com" and len(seg) > 1 and seg[0] == "jobs":
        return info("comeet", seg[1], seg[-1] if len(seg) > 3 else None)

    # Kula: careers.kula.ai/{board}/jobs/{id} or /{board}/{id}
    if host == "careers.kula.ai" and seg:
        return info("kula", seg[0], seg[-1] if len(seg) > 1 else None)

    # GoHire: jobs.gohire.io/{board}/{slug}
    if host == "jobs.gohire.io":
        return info("gohire", seg[0] if seg else None, seg[-1] if len(seg) > 1 else None)

    return info("custom")


_SUFFIXES = {
    "inc", "incorporated", "ltd", "limited", "corp", "corporation", "co", "llc", "llp",
    "lp", "plc", "ulc", "gmbh", "ag", "sa",
}  # fmt: skip


def normalize_employer(name: str | None) -> str | None:
    """'Activision Blizzard, Inc.' -> 'activision blizzard'. None for blank names."""
    if not name:
        return None
    words = re.findall(r"[a-z0-9]+", name.casefold().replace("&", " and "))
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words.pop()
        if len(words) > 1 and words[-1] == "and":  # "& Co."
            words.pop()
    return " ".join(words) or None
