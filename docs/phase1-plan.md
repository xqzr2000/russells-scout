# Phase 1 plan: WorkBC-first scouting slice

Status: **planned** (2026-10-30). Plan: [architecture-plan.md](architecture-plan.md) §9 and §13.
Zero LLM tokens: everything here is plain code that runs on a timer from Phase 2 on.

## 1. What WorkBC gives us (probe on the box, 2026-10-30)

**API.** The search page at workbc.ca loads an Angular app whose `api` setting points to
`https://workbc-jb.a55eb5-prod.stratus.cloud.gov.bc.ca/`. Search is
`POST api/Search/JobSearch` with a JSON body. Source code: `github.com/bcgov/workbc-jb`
(`SearchController.cs`, `JobSearchFilters.cs`, `JobSearchQuery.cs`). The older documented
`workbcjobs.api.gov.bc.ca/v1` returns 404 and is not used.

```json
{"Page": 1, "PageSize": 50, "Keyword": "data scientist", "SearchInField": "title", "SortOrder": 1}
```

- `SearchInField: "title"` matches titles only; words are combined with AND, in any order
  (`"AI engineer"` also matches "Software Engineer, AI Platform"). A quoted phrase is supported.
- `SortOrder: 1` is newest first. Unknown body fields are rejected with 400.
- The response's outer keys are lowercase (`result`, `count`, `pageNumber`, `pageSize`); the job
  fields inside are PascalCase.

**Fields per posting:** `JobId`, `Title`, `EmployerName`, `City`, `Location`, `DatePosted`,
`ExpireDate`, `LastUpdated`, `SalarySummary`, `HoursOfWork`, `PeriodOfEmployment`, `IsFederalJob`,
`WorkplaceType`, `ExternalSource`, `Noc2021`, `Views`, `IsNew`.

**Numbers, all of B.C., title-only:**

| Term | Postings | Term | Postings |
|---|---|---|---|
| data scientist | 19 | machine learning | 21 |
| data science | 5 | ML engineer | 10 |
| data analyst | 19 | AI engineer | 64 |
| business data analyst | 3 | artificial intelligence | 3 |
| data engineer | 52 | quantitative analyst | 3 |

176 unique postings from 199 term hits; about 12 requests for a full sweep. 4 are Job Bank postings.

**Cities:** Vancouver 142, Burnaby 11, Surrey 4, "British Columbia" 4, Victoria 3, Township of
Langley 3, Coquitlam 2, Richmond, West Vancouver, New Westminster, Langley, North Vancouver 1 each,
and two "Virtual job based in …, ON" (Job Bank).

**Where the links go (by host):**

| Group | Postings | Hosts seen |
|---|---|---|
| Public ATS APIs | 33 (19%) | jobs.ashbyhq.com 12, jobs.lever.co 11, job-boards.greenhouse.io 7, company sites with `?gh_jid=` 3 |
| Workday | 37 (21%) | `*.wd*.myworkdayjobs.com`: Workday 19, Remitly 6, Mastercard 4, Aritzia 3, Clio 2, Accenture 2, Xbox 1 |
| Large company sites | ~45 | amazon.jobs 12, jobs.ea.com 11, CCL 8, lululemon 6, Microsoft 2, Capgemini 2, Fraser Health 2, Activision 2 |
| Other ATS | ~17 | Oracle Cloud 5, Workable 3, SmartRecruiters 2, Rippling 2, Comeet 2, GoHire 2, Kula 1 |
| Job Bank | 4 | no link |

The probe listed the top 30 hosts; the remaining ~40 postings are spread over hosts with one posting each.

**Quirks to code for:**
- The jump link is `ExternalSource.Source[0].Url`. `ApplyWebsite` is empty in search results.
- WorkBC's remote filter (`SearchJobTypeVirtual`) only finds Job Bank postings: external postings
  have no `WorkplaceType`. When an external posting has no city, WorkBC uses the province name, so
  `City = "British Columbia"` usually means remote.
- External postings' `ExpireDate` is always `DatePosted` + 90 days, so it says nothing about
  whether the job is still open.
- The same job can appear under several employer names and links: one Activision role
  (requisition `R028031`) appeared as "Activision Blizzard", "Activision Blizzard, Inc." and
  "Activision", on three different sites.

## 2. Search criteria (`config/search.json`)

```json
{
  "terms": ["data scientist", "data science", "data analyst", "business data analyst",
            "data engineer", "machine learning", "ML engineer", "AI engineer",
            "artificial intelligence", "quantitative analyst"],
  "area": {
    "metro": ["Vancouver", "Burnaby", "Richmond", "Surrey", "Coquitlam", "Port Coquitlam",
              "Port Moody", "New Westminster", "North Vancouver", "West Vancouver", "Delta",
              "Langley", "Township of Langley", "Maple Ridge", "Pitt Meadows", "White Rock",
              "Anmore", "Belcarra", "Bowen Island", "Lions Bay", "Tsawwassen", "UBC"],
    "include_remote": true,
    "keep_other_bc": true
  },
  "sweep": {"page_size": 50, "max_pages_per_term": 10, "delay_seconds": 1}
}
```

City names are compared case-insensitively, after removing prefixes such as "City of" and
"District of".

## 3. Pipeline

1. **Sweep** every term, every page, newest first. A term counts as successful only if all its
   pages came back and `count` matched what was fetched. Raw responses are saved under
   `/srv/scout/logs/workbc/<run>/` (kept 14 days) for debugging.
2. **Merge** by `JobId`; `matched_terms` collects every term that found the posting.
3. **Area:**
   - `remote`: `WorkplaceType.Id == 15141`, a city starting "Virtual job", the city given only as
     "British Columbia" or empty, or "remote" in the title or link.
   - `metro`: the city is on the metro list.
   - `other_bc` / `outside`: everything else.
   - **Workplace:** separately, `remote` / `hybrid` / `onsite` / `unknown`, from the same signals
     plus "hybrid" in the title or link.
4. **Title match:** `exact` if any term appears as a whole phrase in the title (case-insensitive,
   ignoring punctuation; "ML" and "machine learning" are treated as the same, and so are "AI" and
   "artificial intelligence"); otherwise `loose`.
5. **Link classifier:** reads the ATS type, board name and posting ID from the jump link:

   | Pattern | ATS | Board / ID |
   |---|---|---|
   | `jobs.ashbyhq.com/{board}/{id}` | ashby | board, id |
   | `job-boards.greenhouse.io/{board}/jobs/{id}`, `boards.greenhouse.io/{board}/jobs/{id}` | greenhouse | board, id |
   | any link with `gh_jid={id}` | greenhouse | board unknown (agent finds it), id |
   | `jobs.lever.co/{board}/{id}` | lever | board, id |
   | `{tenant}.wd{n}.myworkdayjobs.com/{site}/job/…_{req}` | workday | tenant/site, req |
   | `jobs.smartrecruiters.com/{board}/{id}` | smartrecruiters | board, id |
   | `jobs.workable.com/view/…`, `apply.workable.com/{board}/j/{id}` | workable | board if present |
   | `*.oraclecloud.com` | oracle | — |
   | `ats.rippling.com/{board}/…`, `comeet.com/jobs/{board}/…`, `careers.kula.ai/{board}/…`, `jobs.gohire.io/…` | rippling, comeet, kula, gohire | board if present |
   | anything else | custom | host |
   | no link, `IsFederalJob` | jobbank | — |

6. **Employers:** normalize the name (lowercase; strip punctuation and Inc., Ltd., Corp., LLC,
   Co.), upsert into `employers` with the ATS type and board taken from the link.
7. **Enrichment (Ashby, Greenhouse, Lever):** one request per board per sweep, at most twice a
   day:
   - Ashby: `GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true`
   - Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true`
   - Lever: `GET https://api.lever.co/v0/postings/{board}?mode=json`

   Postings are matched by their ID on the board. The board fills in the description, pay range,
   remote/hybrid flag and location. It also finds the board's other roles that pass the title and
   area rules; those are stored with `found_via = 'ats'`.
8. **Duplicates:** same `JobId` → one row; same canonical link (lowercase host, tracking
   parameters removed, ID parameters such as `gh_jid` kept) → one row. Probable duplicates
   share a `dup_group_id`: same normalized employer and same normalized title within 30 days, or a
   shared requisition ID (such as `R028031`) under related employer names. Flagged, never deleted.
9. **Trust score** (architecture plan §6.2): plus points for a recognized ATS host, Job Bank, or a
   link on a domain that matches the employer; minus points for the red flags it lists, checked in
   descriptions when enrichment provides one.
10. **Change detection:** a hash of title, employer, city, salary text and (when present) the
    description. A change writes a `changed` event.
11. **Removal:** a posting not seen in a sweep where every term succeeded gets `missed_runs + 1`;
    at 3 it becomes `removed`. If it's seen again, it becomes `active` with a `reappeared` event.
    Postings found through an ATS are removed when their board no longer lists them, under the
    same 3-run rule.

## 4. Data model (first migration)

```sql
sources        (id, kind, name, url, status, last_success_at, consecutive_failures)
employers      (id, name, name_norm UNIQUE, ats, ats_board, careers_host, first_seen_at, last_seen_at)
postings       (id, workbc_job_id UNIQUE NULL, found_via, title, employer_id, city, area, workplace,
                date_posted, last_updated, salary_text, hours, period, is_job_bank,
                jump_url, canonical_url, ats, ats_posting_id, description, pay_min, pay_max,
                matched_terms text[], match_quality, dup_group_id, trust_score, trust_reasons text[],
                content_hash, status, first_seen_at, last_seen_at, missed_runs)
posting_events (id, posting_id, at, kind, details jsonb)
scout_runs     (id, started_at, finished_at, kind, ok, terms_ok, terms_failed, notes)
run_source_results (run_id, source_id, outcome, count, error)
```

Views: `v_new_relevant_7d` (`area` in metro/remote, `match_quality = 'exact'`, new in 7 days),
`v_review` (loose matches), `v_suspicious`, `v_source_health`. Roles as in the architecture
plan §8. Postgres runs in a container on the box, listening on localhost only, with data in
`/srv/scout/pgdata`.

## 5. Decisions (2026-10-30)

- **Area:** Metro Vancouver, plus remote roles wherever they're based. Other B.C. postings are
  kept but marked out of area.
- **Job Bank:** kept, flagged as having no employer site.
- **Title search stays broad.** WorkBC requires all the words of a term to appear, in any order,
  so "AI engineer" also matches software jobs that only mention AI. Rather than narrow the
  search and risk missing real matches, every posting is kept and marked `exact` or `loose`.
  Exact matches are what the digest leads with; loose ones go to `v_review` to check by eye. After
  a few weeks of results, we tune the terms. This also starts the "similar jobs" idea for Phase 4.
- **Workday waits for the agent.** Workday is the largest group (21%), but WorkBC already gives us
  the title, employer, city and link for those postings. Workday has no official public API, and
  its unofficial one is the kind of moving target the agent is meant to handle. It becomes the
  agent's first real task in Phase 3. Ashby, Greenhouse and Lever have documented public APIs and
  are built in Phase 1.
- **Data use:** WorkBC data stays in the private database and is never republished. Because the
  repo is public, **test fixtures are synthetic**: same structure as WorkBC's responses, made-up
  employers and titles. The real probe responses stay on the box (and in your WSL2 log copy) for an
  optional local replay test that is skipped when the files aren't there.

## 6. Build order

| # | Task | Done when |
|---|---|---|
| 1.1 | Postgres service in `compose.yaml` (profile `scout`), `/srv/scout/pgdata`, first migration, roles, views | `scout db check` connects as each role; `scout_reader` can't write |
| 1.2 | `config/search.json` + loader with validation | bad config fails with a clear message |
| 1.3 | WorkBC client: request body, paging, per-term success, raw-response saving, polite delay | unit tests on synthetic fixtures (paging, empty term, malformed response) |
| 1.4 | Area, workplace and title-match rules | table-driven tests, including every city and remote case in §1 |
| 1.5 | Link classifier and employer normalizer | one test per row of the table in §3 step 5 |
| 1.6 | Upsert, events, change detection, removal safety | rerun on the same fixture → no new rows; failed term → nothing marked missed |
| 1.7 | Duplicate grouping | the Activision case (3 names, 3 links, 1 requisition) lands in one group |
| 1.8 | Ashby, Greenhouse, Lever enrichment | synthetic board fixtures; a failing board doesn't stop the sweep |
| 1.9 | Trust scoring | the plan §6.2 red flags each have a test |
| 1.10 | `scout sweep` CLI (`--dry-run` prints without writing) and a box setup section | a live run on the box loads the real postings; second run adds nothing new |

Development happens with fixtures (Codespaces or this repo's tests); the live check is
`scout sweep --dry-run` on the box, then a real run.

## 7. Exit criteria (acceptance tests from the plan §14)

1. **Zero tokens:** a sweep makes no LLM calls (test 1).
2. **Idempotent:** a second sweep with unchanged data adds no rows and no events (test 2).
3. **Isolation:** a failing term or ATS board doesn't affect the others (test 3).
4. **Removal safety:** a sweep with any failed term marks nothing missed (test 4).
5. **Trust:** seeded scam postings are flagged with the right reasons and kept (test 12).
6. **Reader role:** `scout_reader` can read everything and change nothing (test 15).
7. **Live run:** on the box, the real sweep loads around 170+ postings, with areas, ATS types and
   duplicate groups that look right when checked by hand.

## 8. Not in Phase 1

Scheduling, digest email and backups (Phase 2); Workday, Oracle, SmartRecruiters and custom-site
adapters (agent, Phase 3); wider matching for similar jobs (Phase 4); Crawl4AI for career pages.
