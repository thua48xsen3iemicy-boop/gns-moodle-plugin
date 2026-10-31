-- Authoring sessions have their own identity; ordinary activity lookups stay unchanged.
ALTER TABLE sessions ALTER COLUMN instance_id DROP NOT NULL;
ALTER TABLE workspaces ALTER COLUMN instance_id DROP NOT NULL;
ALTER TABLE sessions ADD COLUMN mode text CHECK (mode IS NULL OR mode = 'authoring');
ALTER TABLE sessions ADD COLUMN library_lab_id bigint;
ALTER TABLE workspaces ADD COLUMN library_lab_id bigint;
ALTER TABLE sessions ADD CONSTRAINT sessions_authoring_identity CHECK (
    (mode IS NULL AND instance_id IS NOT NULL AND library_lab_id IS NULL) OR
    (mode = 'authoring' AND instance_id IS NULL AND library_lab_id > 0 AND lab_def IS NOT NULL));
CREATE UNIQUE INDEX authoring_workspace_per_definition
    ON workspaces (site, user_id, library_lab_id, lab_key) WHERE library_lab_id IS NOT NULL;
DROP INDEX sessions_one_live_per_user;
CREATE UNIQUE INDEX sessions_one_live_per_user ON sessions (site, user_id)
    WHERE status NOT IN ('stopped', 'failed') AND mode IS NULL;
CREATE UNIQUE INDEX sessions_one_live_per_authoring_lab ON sessions (site, user_id, library_lab_id)
    WHERE status NOT IN ('stopped', 'failed') AND mode = 'authoring';

CREATE TABLE derived_images (
    id text PRIMARY KEY, site text NOT NULL, file text NOT NULL UNIQUE,
    base text NOT NULL, base_file text NOT NULL, base_sha256 text NOT NULL,
    sha256 text NOT NULL, size bigint NOT NULL, author_user_id bigint NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now(), title text NOT NULL,
    description text NOT NULL DEFAULT '', library_lab_id bigint NOT NULL,
    node text NOT NULL, supersedes text, featured boolean NOT NULL DEFAULT false,
    retired boolean NOT NULL DEFAULT false
);
CREATE INDEX derived_images_site ON derived_images(site);
CREATE TABLE image_captures (
    session_id text PRIMARY KEY REFERENCES sessions(id), student_key text NOT NULL,
    state text NOT NULL CHECK (state IN ('running', 'done', 'failed')),
    request jsonb NOT NULL, completed jsonb NOT NULL DEFAULT '[]', result jsonb,
    error jsonb, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX capture_one_per_student ON image_captures(student_key) WHERE state = 'running';
