-- Student whose directory may be mounted read-write in this slot's CT (set before the CT is
-- started for a session, cleared once the CT is confirmed stopped). A new session of that
-- student is refused while it is set, so one directory is never writable in two slots.
ALTER TABLE slots ADD COLUMN student_key text;

-- Existing slots: anything not free may still hold the student of its last session.
UPDATE slots SET student_key = sessions.student_key FROM sessions
 WHERE sessions.id = slots.session_id AND slots.state <> 'free';
