-- Deleting student work (CONTRACTS §2.10). A workspace marked for purge is deleted from the database, with its
-- sessions, as soon as none of them is live; its project directory waits in project_purges until a node agent
-- has deleted it (the manager cannot write CephFS).
ALTER TABLE workspaces ADD COLUMN purge_requested_at timestamptz;
CREATE INDEX workspaces_purge ON workspaces (purge_requested_at) WHERE purge_requested_at IS NOT NULL;

CREATE TABLE project_purges (
    project_id      uuid NOT NULL,
    student_key     text NOT NULL,
    requested_at    timestamptz NOT NULL DEFAULT now(),
    attempts        integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_error      text,
    PRIMARY KEY (project_id, student_key)
);
CREATE INDEX project_purges_due ON project_purges (next_attempt_at);

-- Authoring projects a changed definition left behind (manager 0.14 detached them, AGENT_REQUESTS "Долг").
UPDATE workspaces SET purge_requested_at = now() WHERE instance_id IS NULL AND library_lab_id IS NULL;
