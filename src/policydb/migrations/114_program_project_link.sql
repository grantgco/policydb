-- Migration 114: Link programs to projects/locations
-- Adds project_id FK on programs table so programs can be scoped to a location or construction project.
-- ON DELETE CASCADE: deleting a location cascades to its programs.

ALTER TABLE programs ADD COLUMN project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_programs_project ON programs(project_id);
