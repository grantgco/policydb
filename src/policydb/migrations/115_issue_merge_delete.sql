-- Migration 115: Add merged_into_id column for issue merge tracking
ALTER TABLE activity_log ADD COLUMN merged_into_id INTEGER REFERENCES activity_log(id);
