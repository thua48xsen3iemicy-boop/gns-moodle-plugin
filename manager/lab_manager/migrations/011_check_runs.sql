-- Auto-check runs of a session (docs/autochecks/README.md, CONTRACTS.md 2.9). A run is computed over one facts
-- snapshot (facts_rev) with the grading settings of the activity, and kept as it was shown: it is never recomputed.
CREATE TABLE check_runs (
    id                   text        PRIMARY KEY,                     -- 'c-' + 16 hex
    session_id           text        NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    status               text        NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
    requested_by_user_id bigint      NOT NULL,
    requested_at         timestamptz NOT NULL DEFAULT now(),
    started_at           timestamptz,
    finished_at          timestamptz,
    facts_rev            integer,
    checks_def           text,                                        -- sha256 of the gns3lab.checks/1 document
    grading              jsonb       NOT NULL,
    result               jsonb,
    error                jsonb
);

-- One run in progress per session.
CREATE UNIQUE INDEX check_runs_one_active ON check_runs (session_id) WHERE status IN ('queued', 'running');
CREATE INDEX check_runs_by_session ON check_runs (session_id, requested_at DESC);
