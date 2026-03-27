CREATE TABLE IF NOT EXISTS programs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    program_uid         TEXT NOT NULL UNIQUE,
    client_id           INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name                TEXT NOT NULL DEFAULT '',
    line_of_business    TEXT DEFAULT '',
    effective_date      DATE,
    expiration_date     DATE,
    renewal_status      TEXT NOT NULL DEFAULT 'Not Started',
    milestone_profile   TEXT DEFAULT '',
    lead_broker         TEXT DEFAULT '',
    placement_colleague TEXT DEFAULT '',
    account_exec        TEXT NOT NULL DEFAULT 'Grant',
    notes               TEXT DEFAULT '',
    working_notes       TEXT DEFAULT '',
    last_reviewed_at    DATETIME,
    review_cycle        TEXT DEFAULT '1w',
    archived            INTEGER NOT NULL DEFAULT 0,
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_programs_client ON programs(client_id);
CREATE INDEX IF NOT EXISTS idx_programs_uid ON programs(program_uid);
