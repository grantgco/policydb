-- Import field provenance: per-field audit trail tracking which source
-- set which value, when, and what it replaced.

CREATE TABLE IF NOT EXISTS import_field_provenance (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id         INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    field_name        TEXT NOT NULL,
    value             TEXT,
    source_session_id INTEGER REFERENCES import_sessions(id),
    source_name       TEXT,
    as_of_date        DATE,
    applied_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    was_conflict      INTEGER DEFAULT 0,
    prior_value       TEXT
);

CREATE INDEX IF NOT EXISTS idx_provenance_policy ON import_field_provenance(policy_id, field_name);
CREATE INDEX IF NOT EXISTS idx_provenance_session ON import_field_provenance(source_session_id);
