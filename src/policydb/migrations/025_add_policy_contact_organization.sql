-- Migration 025: Add organization column to policy_contacts
-- Allows identification of which firm/company each contact belongs to
-- (e.g., distinguishes internal colleagues from carrier underwriters)

ALTER TABLE policy_contacts ADD COLUMN organization TEXT;
