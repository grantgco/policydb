-- Allow chart_snapshots without a client (for manual chart library)
CREATE TABLE chart_snapshots_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER REFERENCES clients(id),
    chart_type TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO chart_snapshots_new SELECT * FROM chart_snapshots;
DROP TABLE chart_snapshots;
ALTER TABLE chart_snapshots_new RENAME TO chart_snapshots;

CREATE INDEX IF NOT EXISTS idx_chart_snapshots_client
    ON chart_snapshots(client_id, chart_type);
CREATE INDEX IF NOT EXISTS idx_chart_snapshots_manual
    ON chart_snapshots(chart_type) WHERE client_id IS NULL;
