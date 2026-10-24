-- Students whose labs failed one after another on the slot, without a clean session in between
-- (student_key, no repeats). Enough different ones and the slot is taken out of service.
ALTER TABLE slots ADD COLUMN lab_failures jsonb NOT NULL DEFAULT '[]'::jsonb;
