-- Forecast of a session (CONTRACTS.md 2.9a): the rules and findings of the latest facts snapshot, kept while the lab
-- runs and after it stops. Graded on request under the grading of the activity; never a check run, never a mark.
ALTER TABLE sessions
    ADD COLUMN forecast jsonb,
    ADD COLUMN forecast_rev integer,       -- facts_rev the forecast was assessed from
    ADD COLUMN forecast_at timestamptz;
