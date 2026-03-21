-- Migration 027: Add access_point field to policies.
-- Stores access/entry point information for the insured location or risk.
ALTER TABLE policies ADD COLUMN access_point TEXT;
