-- Labs defined in Moodle (plugin round 4). A session and its workspace run either a catalog lab
-- (lab_ref) or a definition (lab_def = sha256 of the normalized definition, computed by the
-- manager). lab_key is whichever of the two is set: one workspace per student, activity and lab.
ALTER TABLE workspaces ALTER COLUMN lab_ref DROP NOT NULL;
ALTER TABLE workspaces ADD COLUMN lab_def text;
ALTER TABLE workspaces ADD COLUMN definition jsonb;  -- Normalized definition, gns3lab.lab/1
ALTER TABLE workspaces ADD COLUMN lab_key text;
UPDATE workspaces SET lab_key = lab_ref;
ALTER TABLE workspaces ALTER COLUMN lab_key SET NOT NULL;
ALTER TABLE workspaces DROP CONSTRAINT workspaces_site_user_id_instance_id_lab_ref_key;
ALTER TABLE workspaces ADD CONSTRAINT workspaces_one_per_lab UNIQUE (site, user_id, instance_id, lab_key);
ALTER TABLE workspaces ADD CONSTRAINT workspaces_one_lab
    CHECK ((lab_ref IS NULL) <> (lab_def IS NULL) AND (lab_def IS NULL) = (definition IS NULL));

ALTER TABLE sessions ALTER COLUMN lab_ref DROP NOT NULL;
ALTER TABLE sessions ADD COLUMN lab_def text;
ALTER TABLE sessions ADD CONSTRAINT sessions_one_lab CHECK ((lab_ref IS NULL) <> (lab_def IS NULL));
