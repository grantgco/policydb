-- Import sessions: tracks each import/reconcile run with metadata.
-- Enables data lineage, column mapping memory, and duplicate file detection.

CREATE TABLE IF NOT EXISTS import_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name     TEXT NOT NULL,
    source_type     TEXT NOT NULL DEFAULT 'csv',
    file_name       TEXT,
    file_hash       TEXT,
    as_of_date      DATE,
    client_id       INTEGER REFERENCES clients(id),
    imported_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    row_count       INTEGER DEFAULT 0,
    matched_count   INTEGER DEFAULT 0,
    created_count   INTEGER DEFAULT 0,
    updated_count   INTEGER DEFAULT 0,
    skipped_count   INTEGER DEFAULT 0,
    notes           TEXT DEFAULT '',
    column_mapping  TEXT,
    status          TEXT DEFAULT 'completed'
);

CREATE INDEX IF NOT EXISTS idx_import_sessions_source ON import_sessions(source_name);
CREATE INDEX IF NOT EXISTS idx_import_sessions_client ON import_sessions(client_id);
