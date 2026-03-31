-- Migration 119: Track which issue activities were moved from during merge (for dissolve)
ALTER TABLE activity_log ADD COLUMN merged_from_issue_id INTEGER REFERENCES activity_log(id);
