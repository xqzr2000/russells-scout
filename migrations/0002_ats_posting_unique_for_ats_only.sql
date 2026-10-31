-- 0002: a job-board posting may appear more than once among WorkBC postings.
--
-- 0001 made (ats, ats_board, ats_posting_id) unique for every posting. WorkBC sometimes lists
-- the same employer posting twice under two JobIds; the second insert would then fail and
-- roll back the whole sweep. Uniqueness now applies only to postings Scout finds directly on
-- a job board (found_via = 'ats', task 1.8). WorkBC duplicates are kept and grouped by
-- dup_group_id (task 1.7).

SET search_path = scout;

ALTER TABLE postings DROP CONSTRAINT postings_ats_ats_board_ats_posting_id_key;

CREATE UNIQUE INDEX postings_ats_posting_unique
    ON postings (ats, ats_board, ats_posting_id)
    WHERE found_via = 'ats';

CREATE INDEX postings_ats_posting_idx
    ON postings (ats, ats_board, ats_posting_id)
    WHERE ats_posting_id IS NOT NULL;

-- Find active WorkBC postings quickly when marking missed ones.
CREATE INDEX postings_found_via_status_idx ON postings (found_via, status);
