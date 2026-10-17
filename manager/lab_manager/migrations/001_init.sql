-- Lab Manager schema v1. Applied once by `python -m lab_manager.admin migrate`.

-- Lab Slot LXCs: interchangeable executors, never bound to a student.
-- Static fields come from MANAGER_SLOTS_FILE; tokens are never stored here.
CREATE TABLE slots (
    vmid          integer PRIMARY KEY,
    address       text NOT NULL UNIQUE,
    port          integer NOT NULL DEFAULT 8080,
    state         text NOT NULL DEFAULT 'maintenance'
                  CHECK (state IN ('free', 'preparing', 'ready', 'busy', 'releasing', 'failed', 'maintenance')),
    -- Fencing token: bumped on every allocation and release. A worker holding an older
    -- value must not touch the slot or its session.
    generation    bigint NOT NULL DEFAULT 0,
    session_id    text,
    in_config     boolean NOT NULL DEFAULT true,  -- false: removed from the slots file
    state_message text NOT NULL DEFAULT '',
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- A student's persistent copy of one lab (LAB_PERSISTENCE=resume): the same project_id and
-- the exact DeployTicket are reused on whichever slot runs the next session.
CREATE TABLE workspaces (
    id           bigserial PRIMARY KEY,
    site         text NOT NULL,    -- Site slug from MANAGER_SITES_FILE
    user_id      bigint NOT NULL,
    instance_id  bigint NOT NULL,
    lab_ref      text NOT NULL,
    project_id   uuid NOT NULL UNIQUE,
    project_name text NOT NULL,
    ticket       jsonb,            -- DeployTicket of the first deploy, replayed unchanged
    deployed     boolean NOT NULL DEFAULT false,
    created_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (site, user_id, instance_id, lab_ref)
);

CREATE TABLE sessions (
    id              text PRIMARY KEY,
    site            text NOT NULL,
    user_id         bigint NOT NULL,
    instance_id     bigint NOT NULL,
    course_id       bigint,
    lab_ref         text NOT NULL,
    workspace_id    bigint NOT NULL REFERENCES workspaces (id),
    student_key     text NOT NULL,   -- Directory name under students/ (node agent student_key)
    project_id      uuid NOT NULL,   -- GNS3 project of this session (per session in fresh mode)
    project_name    text NOT NULL,
    resources       jsonb,           -- Lab requirements used for placement and CT sizing
    slot_node       text,            -- Proxmox node of the slot at allocation (placement)
    status          text NOT NULL
                    CHECK (status IN ('queued', 'starting', 'running', 'stopping', 'stopped', 'failed')),
    stage           text NOT NULL,   -- Internal step of the orchestrator, see orchestrator.py
    status_message  text NOT NULL DEFAULT '',
    error           jsonb,
    slot_vmid       integer REFERENCES slots (vmid),
    slot_generation bigint,
    pve_upid        text,            -- Proxmox task in flight for this session (config change)
    stop_requested  boolean NOT NULL DEFAULT false,
    stop_reason     text,
    max_duration    integer NOT NULL,  -- Minutes
    topology        jsonb,           -- Snapshot of the running project (node_ids of the slot)
    created_at      timestamptz NOT NULL DEFAULT now(),
    stage_started_at timestamptz NOT NULL DEFAULT now(),
    started_at      timestamptz,
    expires_at      timestamptz,
    finished_at     timestamptz,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- One live lab per student on a site (whatever the activity); one live session per slot.
CREATE UNIQUE INDEX sessions_one_live_per_user ON sessions (site, user_id)
    WHERE status NOT IN ('stopped', 'failed');
CREATE UNIQUE INDEX sessions_one_live_per_slot ON sessions (slot_vmid)
    WHERE slot_vmid IS NOT NULL AND status NOT IN ('stopped', 'failed');
CREATE INDEX sessions_lookup ON sessions (site, user_id, instance_id, created_at DESC);

-- Every mutating Lab Slot call. The Idempotency-Key and the exact body are stored before
-- the HTTP request, so a retry after a crash replays the same request.
CREATE TABLE slot_calls (
    id              bigserial PRIMARY KEY,
    session_id      text NOT NULL REFERENCES sessions (id),
    slot_vmid       integer NOT NULL REFERENCES slots (vmid),
    slot_generation bigint NOT NULL,
    action          text NOT NULL CHECK (action IN ('deploy', 'resume', 'stop')),
    idempotency_key text NOT NULL UNIQUE,
    body            jsonb,
    operation_id    uuid,
    status          text NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'accepted', 'succeeded', 'failed', 'abandoned')),
    result          jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX slot_calls_session ON slot_calls (session_id, id DESC);

-- Audit trail for operators. No secrets; user ids stay in the database, not in logs.
CREATE TABLE events (
    id         bigserial PRIMARY KEY,
    at         timestamptz NOT NULL DEFAULT now(),
    session_id text,
    slot_vmid  integer,
    kind       text NOT NULL,
    detail     jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX events_session ON events (session_id, id);
