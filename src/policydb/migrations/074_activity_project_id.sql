-- Migration 074: Add project_id to activity_log for project-level activities.
--
-- Previously, logging an activity against a project created N rows (one per policy).
-- This adds a project_id FK so a single row can represent a project-level activity
-- with policy_id NULL.

ALTER TABLE activity_log ADD COLUMN project_id INTEGER REFERENCES projects(id);

CREATE INDEX IF NOT EXISTS idx_activity_log_project_id ON activity_log(project_id);
