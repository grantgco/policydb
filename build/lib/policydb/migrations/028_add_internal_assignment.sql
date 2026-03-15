-- Migration 028: Add assignment field to client_contacts for internal team members.
-- 'assignment' captures what a specific internal person does for a particular client
-- (e.g. "Lead AE", "Handles construction program"). This is per-client, unlike 'role'
-- which is their general function (shared across all clients).
ALTER TABLE client_contacts ADD COLUMN assignment TEXT;
