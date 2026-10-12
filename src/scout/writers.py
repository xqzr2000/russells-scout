"""Where results go. Stage 1: JSON files. Stage 3 adds a Postgres writer with the same interface."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from scout.models import JobPosting


def write_json(data: dict | list, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_postings(postings: Iterable[JobPosting], path: Path) -> Path:
    return write_json([p.to_dict() for p in postings], path)
