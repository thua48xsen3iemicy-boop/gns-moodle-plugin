-- Auto-checks of the course activity (CONTRACTS.md 2.9, "Описание из активности"): the gns3lab.checks/1 document the
-- plugin sent for the session, outside the lab and its lab_def. NULL: the lab's own checks are used.
ALTER TABLE sessions
    ADD COLUMN checks jsonb,            -- normalized document of the activity
    ADD COLUMN checks_def text,         -- sha256 of the description in force (activity or lab); NULL: none or unknown
    ADD COLUMN checks_error jsonb;      -- the activity sent a document that does not fit the lab: no checks then
