-- Facts collected from the guests of a running lab (docs/DESIGN.md 6.7).
-- session_facts keeps the latest result per node and collector, session_facts_log the changes only
-- (new data, a collection that started or stopped failing). Both go away with their session.
ALTER TABLE sessions
    ADD COLUMN facts_rev integer NOT NULL DEFAULT 0,        -- grows on every journalled change
    ADD COLUMN facts_polled_at timestamptz,                 -- last successful read of the slot's facts
    ADD COLUMN facts_refresh_at timestamptz;                -- the plugin asked for a collection now

CREATE TABLE session_facts (
    session_id   text        NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    node_id      text        NOT NULL,
    node_name    text        NOT NULL,
    collector    text        NOT NULL,                      -- 'linux.net'
    schema       text        NOT NULL,                      -- 'linux.net/1'
    kind         text        NOT NULL DEFAULT 'state',
    observed_at  timestamptz,                               -- last successful collection; NULL: never
    attempted_at timestamptz NOT NULL,
    ok           boolean     NOT NULL,
    error        jsonb,
    data         jsonb,                                     -- last known; NULL until the first success
    PRIMARY KEY (session_id, node_id, collector)
);

CREATE TABLE session_facts_log (
    id           bigserial   PRIMARY KEY,
    session_id   text        NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    rev          integer     NOT NULL,                      -- sessions.facts_rev after this change
    recorded_at  timestamptz NOT NULL DEFAULT now(),
    node_id      text        NOT NULL,
    node_name    text        NOT NULL,
    collector    text        NOT NULL,
    schema       text        NOT NULL,
    kind         text        NOT NULL DEFAULT 'state',
    observed_at  timestamptz,
    attempted_at timestamptz NOT NULL,
    ok           boolean     NOT NULL,
    error        jsonb,
    data         jsonb
);

CREATE INDEX session_facts_log_by_node ON session_facts_log (session_id, node_id, collector, id);
