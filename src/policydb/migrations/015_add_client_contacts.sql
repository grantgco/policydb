CREATE TABLE IF NOT EXISTS client_contacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    title       TEXT,
    email       TEXT,
    phone       TEXT,
    notes       TEXT,
    is_primary  INTEGER NOT NULL DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_client_contacts_client ON client_contacts(client_id);
