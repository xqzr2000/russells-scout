-- 0003: a view of duplicate groups (task 1.7), to check grouping by eye.
-- Every posting that shares a dup_group_id with another, group by group.

SET search_path = scout;

CREATE VIEW v_duplicates AS
SELECT p.dup_group_id, p.id, p.status, p.title,
       COALESCE(e.name, p.employer_name) AS employer, p.city, p.first_seen_at, p.jump_url
FROM postings p
LEFT JOIN employers e ON e.id = p.employer_id
WHERE p.dup_group_id IS NOT NULL
ORDER BY p.dup_group_id, p.id;
