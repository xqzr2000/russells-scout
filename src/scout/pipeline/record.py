"""One raw WorkBC job -> one Scout posting record (tasks 1.4–1.5 combined).

`build()` applies every rule that needs nothing but the job itself and the search
settings. Task 1.6 saves these records to Postgres; duplicates (1.7), enrichment (1.8)
and trust (1.9) add to them later.
"""

from __future__ import annotations

from dataclasses import dataclass

from scout.config import SearchConfig
from scout.pipeline import classify
from scout.pipeline.links import classify_link, normalize_employer
from scout.sources.workbc import jump_url


def _first_description(value: object) -> str | None:
    if isinstance(value, dict):
        d = value.get("Description")
        if isinstance(d, list) and d:
            return ", ".join(str(x) for x in d)
        if isinstance(d, str):
            return d
    return None


@dataclass(frozen=True)
class PostingRecord:
    workbc_job_id: str
    title: str
    employer_name: str | None
    employer_norm: str | None
    city: str | None
    area: str
    workplace: str
    remote_signs: tuple[str, ...]
    match_quality: str
    exact_terms: tuple[str, ...]
    matched_terms: tuple[str, ...]
    is_job_bank: bool
    jump_url: str | None
    canonical_url: str | None
    ats: str
    ats_board: str | None
    ats_posting_id: str | None
    link_host: str | None
    date_posted: str | None
    last_updated: str | None
    salary_text: str | None
    hours: str | None
    period: str | None

    @property
    def relevant(self) -> bool:
        """What the main view shows: Metro Vancouver or remote, title is a strong match."""
        return self.area in ("metro", "remote") and self.match_quality == "exact"


def build(
    job: dict, matched_terms: list[str] | tuple[str, ...], cfg: SearchConfig
) -> PostingRecord:
    url = jump_url(job)
    link = classify_link(url)
    match = classify.title_match(job.get("Title") or "", cfg.terms)
    return PostingRecord(
        workbc_job_id=str(job["JobId"]),
        title=" ".join(str(job.get("Title") or "").split()),
        employer_name=job.get("EmployerName") or None,
        employer_norm=normalize_employer(job.get("EmployerName")),
        city=job.get("City") or None,
        area=classify.area(job, cfg.area.metro),
        workplace=classify.workplace(job),
        remote_signs=tuple(classify.remote_signs(job)),
        match_quality=match.quality,
        exact_terms=match.exact_terms,
        matched_terms=tuple(matched_terms),
        is_job_bank=bool(job.get("IsFederalJob")) or link.ats == "jobbank",
        jump_url=url,
        canonical_url=link.canonical_url,
        ats=link.ats,
        ats_board=link.board,
        ats_posting_id=link.posting_id,
        link_host=link.host,
        date_posted=job.get("DatePosted"),
        last_updated=job.get("LastUpdated"),
        salary_text=job.get("SalarySummary") or None,
        hours=_first_description(job.get("HoursOfWork")),
        period=_first_description(job.get("PeriodOfEmployment")),
    )
