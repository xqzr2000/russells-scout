-- 0004: columns for job-board enrichment (task 1.8).
--
-- employers.board_checked_at   when Scout last tried to find a Greenhouse board for an
--                              employer whose links carry only ?gh_jid= (re-tried weekly)
-- postings.ats_workplace       remote / hybrid / onsite as the job board states it; preferred
--                              over Scout's own guess from the WorkBC title and link
-- postings.location_text       the job board's location text, e.g. "Remote (Canada)"
-- postings.enriched_at         last time the job board's details were applied

SET search_path = scout;

ALTER TABLE employers ADD COLUMN board_checked_at timestamptz;

ALTER TABLE postings
    ADD COLUMN ats_workplace text CHECK (ats_workplace IN ('onsite', 'hybrid', 'remote', 'unknown')),
    ADD COLUMN location_text text,
    ADD COLUMN enriched_at timestamptz;

-- Main view: the job board's workplace wins; pay and how the posting was found are added.
CREATE OR REPLACE VIEW v_new_relevant_7d AS
SELECT p.id, p.first_seen_at, p.title, COALESCE(e.name, p.employer_name) AS employer,
       p.city, p.area, COALESCE(NULLIF(p.ats_workplace, 'unknown'), p.workplace) AS workplace,
       p.salary_text, p.date_posted, p.is_job_bank,
       p.jump_url, p.trust_score, p.dup_group_id, p.matched_terms,
       p.pay_min, p.pay_max, p.pay_currency, p.pay_period, p.found_via
FROM postings p
LEFT JOIN employers e ON e.id = p.employer_id
WHERE p.status = 'active'
  AND p.area IN ('metro', 'remote')
  AND p.match_quality = 'exact'
  AND p.first_seen_at > now() - interval '7 days'
ORDER BY p.first_seen_at DESC;
