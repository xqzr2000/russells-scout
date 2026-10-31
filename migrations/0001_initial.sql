-- 0001_initial: Phase 1 tables, grants and views (docs/phase1-plan.md §4).
-- Applied by `scout db migrate` as scout_migrate, inside one transaction.
-- Never edit an applied migration; add a new numbered file instead.

SET search_path = scout;

-- Where data comes from: the WorkBC search, and one row per ATS board used for enrichment.
CREATE TABLE sources (
    id                   bigserial PRIMARY KEY,
    kind                 text NOT NULL CHECK (kind IN ('workbc', 'ats')),
    name                 text NOT NULL UNIQUE,          -- e.g. 'workbc', 'ashby:jobber'
    url                  text,
    status               text NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'degraded', 'restricted', 'disabled')),
    last_success_at      timestamptz,
    consecutive_failures integer NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    created_at           timestamptz NOT NULL DEFAULT now()
);

-- One row per employer, discovered from posting links.
CREATE TABLE employers (
    id            bigserial PRIMARY KEY,
    name          text NOT NULL,                        -- as first seen
    name_norm     text NOT NULL UNIQUE,                 -- lowercase, no punctuation or Inc./Ltd.
    ats           text,                                 -- ashby, greenhouse, lever, workday, custom, ...
    ats_board     text,
    careers_host  text,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now()
);

-- One row per sweep or enrichment run.
CREATE TABLE scout_runs (
    id           bigserial PRIMARY KEY,
    kind         text NOT NULL CHECK (kind IN ('sweep', 'enrich')),
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    ok           boolean,                               -- NULL while running
    terms_ok     integer NOT NULL DEFAULT 0,
    terms_failed integer NOT NULL DEFAULT 0,
    notes        text
);

-- Per-source outcome of each run (plan §11 failure classes).
CREATE TABLE run_source_results (
    run_id    bigint NOT NULL REFERENCES scout_runs (id),
    source_id bigint NOT NULL REFERENCES sources (id),
    outcome   text NOT NULL
              CHECK (outcome IN ('ok', 'empty', 'structural', 'transient', 'restricted')),
    count     integer,
    error     text,
    PRIMARY KEY (run_id, source_id)
);

-- One row per listing. Rows are never deleted: closed jobs become status 'removed'.
CREATE TABLE postings (
    id             bigserial PRIMARY KEY,
    workbc_job_id  text UNIQUE,                         -- NULL for postings found only via an ATS
    found_via      text NOT NULL CHECK (found_via IN ('workbc', 'ats')),
    source_id      bigint REFERENCES sources (id),
    title          text NOT NULL,
    employer_id    bigint REFERENCES employers (id),
    employer_name  text,                                -- as shown by the source
    city           text,
    area           text NOT NULL DEFAULT 'unknown'
                   CHECK (area IN ('metro', 'remote', 'other_bc', 'outside', 'unknown')),
    workplace      text NOT NULL DEFAULT 'unknown'
                   CHECK (workplace IN ('onsite', 'hybrid', 'remote', 'unknown')),
    date_posted    timestamptz,
    last_updated   timestamptz,
    salary_text    text,
    hours          text,                                -- e.g. Full-time
    period         text,                                -- e.g. Permanent
    is_job_bank    boolean NOT NULL DEFAULT false,
    jump_url       text,                                -- link to the employer's site or ATS
    canonical_url  text,
    ats            text,
    ats_board      text,
    ats_posting_id text,
    description    text,                                -- from ATS enrichment
    pay_min        numeric,
    pay_max        numeric,
    pay_currency   text,
    pay_period     text,
    matched_terms  text[] NOT NULL DEFAULT '{}',
    match_quality  text NOT NULL DEFAULT 'loose' CHECK (match_quality IN ('exact', 'loose')),
    dup_group_id   bigint,                              -- postings that look like the same job
    trust_score    integer,
    trust_reasons  text[] NOT NULL DEFAULT '{}',
    content_hash   text,
    status         text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'removed')),
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),
    missed_runs    integer NOT NULL DEFAULT 0 CHECK (missed_runs >= 0),
    UNIQUE (ats, ats_board, ats_posting_id)
);
CREATE INDEX postings_canonical_url_idx ON postings (canonical_url);
CREATE INDEX postings_employer_idx      ON postings (employer_id);
CREATE INDEX postings_status_seen_idx   ON postings (status, first_seen_at);
CREATE INDEX postings_dup_group_idx     ON postings (dup_group_id) WHERE dup_group_id IS NOT NULL;

-- Append-only history.
CREATE TABLE posting_events (
    id         bigserial PRIMARY KEY,
    posting_id bigint NOT NULL REFERENCES postings (id),
    run_id     bigint REFERENCES scout_runs (id),
    at         timestamptz NOT NULL DEFAULT now(),
    kind       text NOT NULL CHECK (kind IN ('new', 'changed', 'removed', 'reappeared')),
    details    jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX posting_events_posting_idx ON posting_events (posting_id, at);

-- ---------------------------------------------------------------------------
-- Grants. Data roles get no DELETE, no TRUNCATE and no schema changes.
-- ---------------------------------------------------------------------------
GRANT SELECT ON ALL TABLES IN SCHEMA scout TO scout_reader, scout_ingest, scout_agent;
GRANT INSERT, UPDATE ON sources, employers, scout_runs, run_source_results, postings
    TO scout_ingest, scout_agent;
GRANT INSERT ON posting_events TO scout_ingest, scout_agent;   -- history is append-only
GRANT USAGE ON ALL SEQUENCES IN SCHEMA scout TO scout_ingest, scout_agent;

-- Tables and views added by later migrations stay readable by everyone.
ALTER DEFAULT PRIVILEGES FOR ROLE scout_migrate IN SCHEMA scout
    GRANT SELECT ON TABLES TO scout_reader, scout_ingest, scout_agent;
ALTER DEFAULT PRIVILEGES FOR ROLE scout_migrate IN SCHEMA scout
    GRANT USAGE ON SEQUENCES TO scout_ingest, scout_agent;

-- ---------------------------------------------------------------------------
-- Views for humans (architecture plan §8).
-- ---------------------------------------------------------------------------

-- New jobs worth a look: Metro Vancouver or remote, a search term appears as a phrase in the title.
CREATE VIEW v_new_relevant_7d AS
SELECT p.id, p.first_seen_at, p.title, COALESCE(e.name, p.employer_name) AS employer,
       p.city, p.area, p.workplace, p.salary_text, p.date_posted, p.is_job_bank,
       p.jump_url, p.trust_score, p.dup_group_id, p.matched_terms
FROM postings p
LEFT JOIN employers e ON e.id = p.employer_id
WHERE p.status = 'active'
  AND p.area IN ('metro', 'remote')
  AND p.match_quality = 'exact'
  AND p.first_seen_at > now() - interval '7 days'
ORDER BY p.first_seen_at DESC;

-- Looser title matches, to check by eye and tune the search terms.
CREATE VIEW v_review AS
SELECT p.id, p.first_seen_at, p.title, COALESCE(e.name, p.employer_name) AS employer,
       p.city, p.area, p.matched_terms, p.jump_url
FROM postings p
LEFT JOIN employers e ON e.id = p.employer_id
WHERE p.status = 'active'
  AND p.area IN ('metro', 'remote')
  AND p.match_quality = 'loose'
ORDER BY p.first_seen_at DESC;

-- Listings whose trust score went negative (red flags outweigh good signs), with the reasons.
CREATE VIEW v_suspicious AS
SELECT p.id, p.first_seen_at, p.title, COALESCE(e.name, p.employer_name) AS employer,
       p.trust_score, p.trust_reasons, p.jump_url, p.status
FROM postings p
LEFT JOIN employers e ON e.id = p.employer_id
WHERE p.trust_score < 0
ORDER BY p.first_seen_at DESC;

-- Each source with its most recent run outcome.
CREATE VIEW v_source_health AS
SELECT s.id, s.kind, s.name, s.status, s.last_success_at, s.consecutive_failures,
       last.outcome AS last_outcome, last.count AS last_count, last.error AS last_error,
       last.started_at AS last_run_at
FROM sources s
LEFT JOIN LATERAL (
    SELECT r.outcome, r.count, r.error, sr.started_at
    FROM run_source_results r
    JOIN scout_runs sr ON sr.id = r.run_id
    WHERE r.source_id = s.id
    ORDER BY sr.started_at DESC
    LIMIT 1
) last ON true
ORDER BY s.kind, s.name;

GRANT SELECT ON v_new_relevant_7d, v_review, v_suspicious, v_source_health
    TO scout_reader, scout_ingest, scout_agent;
