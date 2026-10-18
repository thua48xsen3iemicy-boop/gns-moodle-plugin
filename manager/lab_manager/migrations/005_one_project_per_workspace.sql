-- LAB_PERSISTENCE is gone: every session of a workspace runs the workspace's project. In the old
-- fresh mode the first session's ticket (with that session's own project_id) was stored here;
-- it belongs to another project and must not be replayed. The next deploy builds a new one.
UPDATE workspaces SET ticket = NULL, deployed = false
 WHERE ticket IS NOT NULL AND ticket->>'project_id' IS DISTINCT FROM project_id::text;
