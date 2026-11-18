-- Teacher scripts of an auto-check (CONTRACTS.md 2.9 `scripts`) run for up to the sum of their timeouts: a run with
-- scripts may take that much longer than CHECK_TIMEOUT before it is failed with TIMEOUT.
ALTER TABLE check_runs ADD COLUMN extra_seconds integer NOT NULL DEFAULT 0 CHECK (extra_seconds >= 0);
