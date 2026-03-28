-- Backfill updated_at from audit_log where available
UPDATE clients SET updated_at = COALESCE(
    (SELECT MAX(changed_at) FROM audit_log WHERE table_name = 'clients' AND row_id = CAST(clients.id AS TEXT) AND operation = 'UPDATE'),
    DATETIME('now')
);
UPDATE policies SET updated_at = COALESCE(
    (SELECT MAX(changed_at) FROM audit_log WHERE table_name = 'policies' AND row_id = CAST(policies.id AS TEXT) AND operation = 'UPDATE'),
    DATETIME('now')
);

-- Auto-update triggers
CREATE TRIGGER IF NOT EXISTS clients_set_updated_at
AFTER UPDATE ON clients
BEGIN
    UPDATE clients SET updated_at = DATETIME('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS policies_set_updated_at
AFTER UPDATE ON policies
BEGIN
    UPDATE policies SET updated_at = DATETIME('now') WHERE id = NEW.id;
END;

-- Index for staleness queries
CREATE INDEX IF NOT EXISTS idx_clients_updated_at ON clients(updated_at);
CREATE INDEX IF NOT EXISTS idx_policies_updated_at ON policies(updated_at);
