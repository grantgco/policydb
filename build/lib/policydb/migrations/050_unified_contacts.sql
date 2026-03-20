-- Migration 050: Unified contacts schema
-- Single contacts table with junction tables for client and policy assignments

CREATE TABLE IF NOT EXISTS contacts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    email        TEXT,
    phone        TEXT,
    mobile       TEXT,
    organization TEXT,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_name_lower ON contacts(LOWER(TRIM(name)));

CREATE TABLE IF NOT EXISTS contact_client_assignments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id    INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    client_id     INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    contact_type  TEXT NOT NULL DEFAULT 'client',
    role          TEXT,
    title         TEXT,
    assignment    TEXT,
    notes         TEXT,
    is_primary    INTEGER NOT NULL DEFAULT 0,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(contact_id, client_id, contact_type)
);

CREATE INDEX IF NOT EXISTS idx_cca_client ON contact_client_assignments(client_id);
CREATE INDEX IF NOT EXISTS idx_cca_contact ON contact_client_assignments(contact_id);

CREATE TABLE IF NOT EXISTS contact_policy_assignments (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id              INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    policy_id               INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    role                    TEXT,
    title                   TEXT,
    notes                   TEXT,
    is_placement_colleague  INTEGER NOT NULL DEFAULT 0,
    created_at              DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(contact_id, policy_id)
);

CREATE INDEX IF NOT EXISTS idx_cpa_policy ON contact_policy_assignments(policy_id);
CREATE INDEX IF NOT EXISTS idx_cpa_contact ON contact_policy_assignments(contact_id);

-- Add contact_id FK to activity_log
ALTER TABLE activity_log ADD COLUMN contact_id INTEGER REFERENCES contacts(id);
