-- Program contact assignments (mirrors contact_policy_assignments)
CREATE TABLE IF NOT EXISTS contact_program_assignments (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id             INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    program_id             INTEGER NOT NULL REFERENCES programs(id) ON DELETE CASCADE,
    role                   TEXT,
    title                  TEXT,
    notes                  TEXT,
    is_placement_colleague INTEGER DEFAULT 0,
    created_at             DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(contact_id, program_id)
);

CREATE INDEX IF NOT EXISTS idx_cpa_program_id ON contact_program_assignments(program_id);
CREATE INDEX IF NOT EXISTS idx_cpa_contact_id_prog ON contact_program_assignments(contact_id);

-- Program milestones (mirrors policy_milestones but keyed on program_uid)
CREATE TABLE IF NOT EXISTS program_milestones (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    program_uid  TEXT NOT NULL,
    milestone    TEXT NOT NULL,
    completed    INTEGER NOT NULL DEFAULT 0,
    completed_at DATETIME,
    UNIQUE(program_uid, milestone)
);

CREATE INDEX IF NOT EXISTS idx_program_milestones_uid ON program_milestones(program_uid);

-- Add program_uid to client_request_bundles for program-scoped RFIs
ALTER TABLE client_request_bundles ADD COLUMN program_uid TEXT;
