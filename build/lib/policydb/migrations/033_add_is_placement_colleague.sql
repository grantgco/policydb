-- Migration 033: Add is_placement_colleague flag to policy_contacts.
--
-- Replaces the legacy policies.placement_colleague text field with a structured
-- boolean on the contacts row. A policy_contact with is_placement_colleague=1
-- is the designated placement colleague for that policy.
-- The legacy column is preserved (never remove columns) but code stops writing to it.

ALTER TABLE policy_contacts ADD COLUMN is_placement_colleague INTEGER NOT NULL DEFAULT 0;

-- Back-fill from existing role-based data
UPDATE policy_contacts
SET is_placement_colleague = 1
WHERE LOWER(TRIM(role)) = 'placement colleague';
