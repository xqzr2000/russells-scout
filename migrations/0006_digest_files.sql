-- 0006: the digest is written to files on the box instead of emailed (2026-10-31).
--
-- 0005 created `digests` for the email design. Here: sent_at becomes made_at, subject becomes
-- title, and `file` records the digest's file name in /srv/scout/logs/digests (e.g.
-- 2026-11-01-0710.txt). A new migration rather than an edit, since 0005 may already be applied.

SET search_path = scout;

ALTER TABLE digests RENAME COLUMN sent_at TO made_at;
ALTER TABLE digests RENAME COLUMN subject TO title;
ALTER TABLE digests ADD COLUMN file text NOT NULL DEFAULT '';
ALTER TABLE digests ALTER COLUMN file DROP DEFAULT;
