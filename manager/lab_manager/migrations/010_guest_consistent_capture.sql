-- Lifetime history belongs to the project/workspace, not to a transient session.
ALTER TABLE workspaces ADD COLUMN started_nodes text[] NOT NULL DEFAULT '{}';
ALTER TABLE sessions ADD COLUMN node_statuses jsonb NOT NULL DEFAULT '{}';
ALTER TABLE sessions ADD COLUMN node_observed_at timestamptz NOT NULL DEFAULT 'epoch';
UPDATE sessions SET node_statuses = coalesce((
    SELECT jsonb_object_agg(n->>'name', n->>'status')
    FROM jsonb_array_elements(coalesce(topology->'topology'->'nodes', '[]')) n
    WHERE n ? 'name' AND n ? 'status'
), '{}');
UPDATE workspaces w SET started_nodes = ARRAY(
    SELECT DISTINCT n->>'name' FROM sessions s,
        jsonb_array_elements(coalesce(s.topology->'topology'->'nodes', '[]')) n
    WHERE s.workspace_id = w.id AND n->>'status' = 'started'
);
ALTER TABLE image_captures ADD COLUMN force boolean NOT NULL DEFAULT false;
ALTER TABLE image_captures ADD COLUMN phase text NOT NULL DEFAULT 'capture';
ALTER TABLE image_captures ADD COLUMN forced text[] NOT NULL DEFAULT '{}';
ALTER TABLE image_captures ADD COLUMN quiesce jsonb NOT NULL DEFAULT '{}';
-- Captures produced by the previous implementation did not quiesce guests.
ALTER TABLE derived_images ADD COLUMN forced boolean NOT NULL DEFAULT true;
ALTER TABLE derived_images ALTER COLUMN forced SET DEFAULT false;
UPDATE image_captures c SET forced = ARRAY(SELECT n->>'name' FROM jsonb_array_elements(c.request) n)
    WHERE state = 'done';
