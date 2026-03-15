-- Migration 019: add policy_contacts table
-- Stores per-policy team members (placement colleagues, underwriters, brokers)
-- Distinct from client_contacts (contact_type='internal') which is the account-wide team

CREATE TABLE IF NOT EXISTS policy_contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id  INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    title      TEXT,
    role       TEXT,
    phone      TEXT,
    email      TEXT,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Migrate existing placement_colleague text field data
INSERT INTO policy_contacts (policy_id, name, role, email, phone)
SELECT id,
       placement_colleague,
       'Placement Colleague',
       NULLIF(TRIM(COALESCE(placement_colleague_email, '')), ''),
       NULL
FROM policies
WHERE placement_colleague IS NOT NULL AND TRIM(placement_colleague) != '';

-- Migrate existing underwriter_name text field data
INSERT INTO policy_contacts (policy_id, name, role, phone)
SELECT id,
       underwriter_name,
       'Underwriter',
       NULLIF(TRIM(COALESCE(underwriter_contact, '')), '')
FROM policies
WHERE underwriter_name IS NOT NULL AND TRIM(underwriter_name) != '';
