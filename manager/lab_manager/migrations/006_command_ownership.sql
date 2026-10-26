-- Never reset this sequence while any agent/slot may retain an older authority.
CREATE SEQUENCE command_epochs AS bigint;
CREATE TABLE command_owner (singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton), epoch bigint NOT NULL);
INSERT INTO command_owner (epoch) VALUES (0);
ALTER TABLE workspaces ADD COLUMN resource_snapshot jsonb;
UPDATE workspaces w SET resource_snapshot = (
    SELECT s.resources FROM sessions s WHERE s.workspace_id = w.id AND s.resources IS NOT NULL
    ORDER BY s.created_at DESC LIMIT 1
);
