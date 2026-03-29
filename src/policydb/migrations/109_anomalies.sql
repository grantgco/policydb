-- Anomaly detection findings table
CREATE TABLE IF NOT EXISTS anomalies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_key TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'warning',
    client_id INTEGER,
    policy_id INTEGER,
    title TEXT NOT NULL,
    details TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    detected_at TEXT NOT NULL DEFAULT (DATETIME('now')),
    acknowledged_at TEXT,
    resolved_at TEXT,
    scan_id TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_anomalies_status ON anomalies(status);
CREATE INDEX IF NOT EXISTS idx_anomalies_client ON anomalies(client_id);
CREATE INDEX IF NOT EXISTS idx_anomalies_policy ON anomalies(policy_id);
CREATE INDEX IF NOT EXISTS idx_anomalies_rule ON anomalies(rule_key, client_id, policy_id);
