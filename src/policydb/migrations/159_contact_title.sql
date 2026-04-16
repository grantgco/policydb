-- Migration 159: Add title column to contacts (person-level job title)
-- Junction-table title fields remain as per-assignment overrides.

ALTER TABLE contacts ADD COLUMN title TEXT;
