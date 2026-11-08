# Orientation (task 0000)

## How a new ATS plugs in

All in `src/scout/sources/ats.py`; `src/scout/enrich.py` is the only caller and needs no
change for a new API-compatible board.

1. Add the API URL template (a list, if there are alternate hosts) to `URLS`
   (`ats.py:255`) and register the parser in `PARSERS` (`ats.py:254`).
2. Write `parse_<ats>(board, data) -> list[BoardPosting]` (pattern: `parse_ashby`
   `ats.py:144`). Raise `ValueError` on an unexpected shape; return `[]` for an empty
   board. Fill `BoardPosting` (`ats.py:63`): `ats, board, posting_id, title, url`, and as
   available `location/city/country/workplace/description/pay_*/published_at`. Clip
   descriptions with `_clip` (20k) and strip HTML with `text_from_html`.
3. Add the name to `SUPPORTED` (`ats.py:44`). `fetch_board` (`ats.py:275`) then handles
   retries, 404-host fallback, 401/403 -> `restricted`, 5xx -> `transient`, bad JSON ->
   `structural`.
4. `enrich._boards` reads `SELECT DISTINCT ats, ats_board FROM postings ... ats = ANY(SUPPORTED)`
   (`enrich.py:149`), so a board is fetched once a posting carries `ats`/`ats_board`.
   `_apply_board` calls `_match_workbc` / `_save_board_posting` per posting.
5. Tests: add a synthetic fixture under `tests/fixtures/` and a `test_<ats>_board` in
   `tests/test_ats.py`; fetch-board outcome tests reuse the existing fakes.

Non-API boards (custom HTML, Workday, Oracle) are a separate path.

## Test result

`pytest -q tests/test_ats.py tests/test_links.py tests/test_enrich.py`:
**67 passed, 9 skipped** (skips are PostgreSQL tests). venv built from
`requirements-runtime.txt` + `requirements-dev.txt` without errors.

## Workday in boards.csv

**17 boards, 42 active postings** (largest: Workday 8, Remitly 7, Mastercard 5, Aritzia 3,
BDO 3, Clio 3).

## Setup issues

Nothing failed. The only catch: `boards.csv` has commas inside quoted employer names, so
`awk -F,` miscounts; use a CSV parser.
