-- 0005: the daily digest email (Phase 2, tasks 2.3–2.4).
--
-- One row per digest actually sent. The next digest covers everything from the newest
-- row's covers_to, so a day whose email failed is not lost: it is in the next one.
-- Written by scout_ingest (the digest service), readable by everyone (default privileges).

SET search_path = scout;

CREATE TABLE digests (
    id           bigserial PRIMARY KEY,
    sent_at      timestamptz NOT NULL DEFAULT now(),
    covers_from  timestamptz NOT NULL,
    covers_to    timestamptz NOT NULL,
    subject      text NOT NULL,
    new_relevant integer NOT NULL DEFAULT 0,
    new_possible integer NOT NULL DEFAULT 0,
    closed       integer NOT NULL DEFAULT 0,
    suspicious   integer NOT NULL DEFAULT 0,
    problems     integer NOT NULL DEFAULT 0,
    CHECK (covers_to >= covers_from)
);

GRANT INSERT ON digests TO scout_ingest;
