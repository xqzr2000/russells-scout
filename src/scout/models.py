"""The shape of one job posting.

Everything the scout produces ends up as a JobPosting. Today they're written to
JSON files; in stage 3 the same records go into Postgres, so this class is the
contract between the crawler and the database (and the reviewing agent).
"""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class JobPosting:
    company: str
    title: str
    url: str  # absolute link to the posting
    location: str | None = None
    source_url: str | None = None  # the careers page we found it on
    scraped_at: str = field(default_factory=_now)

    @property
    def job_id(self) -> str:
        """Stable ID for de-duplication: the same posting URL always gives the same ID."""
        return hashlib.sha256(self.url.strip().lower().encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {"job_id": self.job_id, **asdict(self)}
