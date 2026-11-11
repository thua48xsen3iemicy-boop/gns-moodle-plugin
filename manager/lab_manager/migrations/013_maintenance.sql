-- Maintenance mode (CONTRACTS §2.11): at most one row. Present = the mode is on; starts_at is when the running
-- labs are stopped. The operator switches it with `lab-manager-admin maintenance on|off`.
CREATE TABLE maintenance (
    id           boolean PRIMARY KEY DEFAULT true CHECK (id),
    starts_at    timestamptz NOT NULL,
    announced_at timestamptz NOT NULL DEFAULT now(),
    message      text NOT NULL DEFAULT ''
);
