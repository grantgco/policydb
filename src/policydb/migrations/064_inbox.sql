CREATE TABLE IF NOT EXISTS inbox (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    inbox_uid    TEXT NOT NULL UNIQUE,
    content      TEXT NOT NULL,
    client_id    INTEGER REFERENCES clients(id),
    status       TEXT NOT NULL DEFAULT 'pending',
    activity_id  INTEGER REFERENCES activity_log(id),
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at DATETIME
);
CREATE INDEX IF NOT EXISTS idx_inbox_status ON inbox(status);
