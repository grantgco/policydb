CREATE TABLE IF NOT EXISTS billing_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    billing_id TEXT NOT NULL,
    description TEXT,
    is_master INTEGER NOT NULL DEFAULT 0,
    parent_billing_id INTEGER REFERENCES billing_accounts(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    modified_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_billing_accounts_client ON billing_accounts(client_id);
