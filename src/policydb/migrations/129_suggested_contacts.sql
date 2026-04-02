CREATE TABLE IF NOT EXISTS suggested_contacts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT NOT NULL,
    parsed_name     TEXT,
    organization    TEXT,
    client_id       INTEGER REFERENCES clients(id),
    client_name     TEXT,
    source_subject  TEXT,
    first_seen_at   TEXT NOT NULL DEFAULT (datetime('now')),
    last_seen_at    TEXT NOT NULL DEFAULT (datetime('now')),
    seen_count      INTEGER NOT NULL DEFAULT 1,
    status          TEXT NOT NULL DEFAULT 'pending',
    blocked         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_suggested_contacts_email ON suggested_contacts(LOWER(TRIM(email)));
