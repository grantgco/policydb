-- Migration 148: Outlook contact sync join key
-- Adds outlook_contact_id to contacts for the PolicyDB -> Outlook push sync.
-- See src/policydb/contact_sync.py for orchestrator and src/policydb/outlook_contacts.py
-- for the AppleScript bridge. Sync is fenced by the "PDB" category on the Outlook side.

ALTER TABLE contacts ADD COLUMN outlook_contact_id TEXT;

CREATE INDEX IF NOT EXISTS idx_contacts_outlook_contact_id
    ON contacts(outlook_contact_id)
    WHERE outlook_contact_id IS NOT NULL;
