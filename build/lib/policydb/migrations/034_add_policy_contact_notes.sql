-- Migration 034: Add notes column to policy_contacts.
-- Allows users to add a note about a contact's role or context for a specific policy.

ALTER TABLE policy_contacts ADD COLUMN notes TEXT;
