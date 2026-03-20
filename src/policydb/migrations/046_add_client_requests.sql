-- Request bundles: aggregated information requests sent to clients
CREATE TABLE IF NOT EXISTS client_request_bundles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id     INTEGER NOT NULL REFERENCES clients(id),
    title         TEXT NOT NULL DEFAULT 'Information Request',
    status        TEXT NOT NULL DEFAULT 'open',
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    sent_at       DATETIME,
    notes         TEXT
);

-- Individual items within a request bundle
CREATE TABLE IF NOT EXISTS client_request_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id     INTEGER NOT NULL REFERENCES client_request_bundles(id) ON DELETE CASCADE,
    description   TEXT NOT NULL,
    policy_uid    TEXT,
    project_name  TEXT,
    category      TEXT,
    received      INTEGER NOT NULL DEFAULT 0,
    received_at   DATETIME,
    notes         TEXT,
    sort_order    INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_request_bundles_client ON client_request_bundles(client_id);
CREATE INDEX IF NOT EXISTS idx_request_items_bundle ON client_request_items(bundle_id);
