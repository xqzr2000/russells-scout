"""Search settings: config/search.json (docs/phase1-plan.md §2, task 1.2).

The file is yours to edit (on GitHub, then rebuild the scout image). Loading checks every
field and stops with a message that names the field and what's wrong, so a typo can't
silently change what Scout searches for. Unknown keys are errors for the same reason.

Location: $SCOUT_CONFIG, or config/search.json at the repo root.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

LIMITS = {
    "page_size": (1, 100),
    "max_pages_per_term": (1, 50),
}
MIN_DELAY_SECONDS = 0.5  # never hit WorkBC faster than twice a second
MAX_TERM_LENGTH = 100


class ConfigError(Exception):
    """search.json is missing or invalid; the message says what to fix."""


@dataclass(frozen=True)
class SweepSettings:
    page_size: int
    max_pages_per_term: int
    delay_seconds: float


@dataclass(frozen=True)
class AreaSettings:
    metro: tuple[str, ...]
    include_remote: bool
    keep_other_bc: bool


@dataclass(frozen=True)
class SearchConfig:
    terms: tuple[str, ...]
    area: AreaSettings
    sweep: SweepSettings
    path: Path


def default_path() -> Path:
    env = os.environ.get("SCOUT_CONFIG")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "config" / "search.json"


def load(path: Path | str | None = None) -> SearchConfig:
    path = Path(path) if path else default_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path}: file not found") from None
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: not valid JSON (line {e.lineno}, column {e.colno})") from None
    return parse(raw, path)


def parse(raw: object, path: Path | str = "search.json") -> SearchConfig:
    path = Path(path)

    def fail(field: str, problem: str) -> ConfigError:
        return ConfigError(f"{path.name}: {field} {problem}")

    def obj(value: object, field: str, keys: set[str]) -> dict:
        if not isinstance(value, dict):
            raise fail(field, "must be an object")
        unknown = sorted(set(value) - keys)
        if unknown:
            raise fail(field, f"has unknown key(s): {', '.join(unknown)}")
        missing = sorted(keys - set(value))
        if missing:
            raise fail(field, f"is missing: {', '.join(missing)}")
        return value

    def strings(value: object, field: str) -> tuple[str, ...]:
        if not isinstance(value, list) or not value:
            raise fail(field, "must be a non-empty list of strings")
        out: list[str] = []
        seen: set[str] = set()
        for i, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                raise fail(f"{field}[{i}]", "must be a non-empty string")
            item = " ".join(item.split())
            if len(item) > MAX_TERM_LENGTH:
                raise fail(f"{field}[{i}]", f"is longer than {MAX_TERM_LENGTH} characters")
            if item.casefold() in seen:
                raise fail(f"{field}[{i}]", f"repeats {item!r}")
            seen.add(item.casefold())
            out.append(item)
        return tuple(out)

    def boolean(value: object, field: str) -> bool:
        if not isinstance(value, bool):
            raise fail(field, f"must be true or false (got {value!r})")
        return value

    def integer(value: object, field: str, name: str) -> int:
        lo, hi = LIMITS[name]
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise fail(field, f"must be a whole number from {lo} to {hi} (got {value!r})")
        return value

    top = obj(raw, "the file", {"terms", "area", "sweep"})
    area = obj(top["area"], "area", {"metro", "include_remote", "keep_other_bc"})
    sweep = obj(top["sweep"], "sweep", {"page_size", "max_pages_per_term", "delay_seconds"})

    delay = sweep["delay_seconds"]
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or delay < MIN_DELAY_SECONDS:
        raise fail("sweep.delay_seconds", f"must be a number ≥ {MIN_DELAY_SECONDS} (got {delay!r})")

    return SearchConfig(
        terms=strings(top["terms"], "terms"),
        area=AreaSettings(
            metro=strings(area["metro"], "area.metro"),
            include_remote=boolean(area["include_remote"], "area.include_remote"),
            keep_other_bc=boolean(area["keep_other_bc"], "area.keep_other_bc"),
        ),
        sweep=SweepSettings(
            page_size=integer(sweep["page_size"], "sweep.page_size", "page_size"),
            max_pages_per_term=integer(
                sweep["max_pages_per_term"], "sweep.max_pages_per_term", "max_pages_per_term"
            ),
            delay_seconds=float(delay),
        ),
        path=path,
    )
