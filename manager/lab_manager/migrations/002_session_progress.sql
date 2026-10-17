-- Progress of the current slot operation, percent (plugin request П2). NULL: unknown.
ALTER TABLE sessions ADD COLUMN progress smallint CHECK (progress BETWEEN 0 AND 100);
