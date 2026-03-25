-- Import source profiles: remembered column mappings and field trust scores per source.
-- When re-uploading from the same source, column mappings auto-load.

CREATE TABLE IF NOT EXISTS import_source_profiles (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name     TEXT NOT NULL UNIQUE,
    display_name    TEXT,
    source_type     TEXT DEFAULT 'csv',
    column_map      TEXT DEFAULT '{}',
    field_trust     TEXT DEFAULT '{}',
    last_used       DATETIME,
    use_count       INTEGER DEFAULT 0,
    notes           TEXT DEFAULT '',
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
