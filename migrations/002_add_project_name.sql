-- Migration 002: add project_name to policies
-- Allows policies to be grouped by project or location (e.g. "Main Street Condos")
ALTER TABLE policies ADD COLUMN project_name TEXT;
