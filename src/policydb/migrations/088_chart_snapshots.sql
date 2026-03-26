-- Chart snapshots: persist edited chart data for recall
CREATE TABLE IF NOT EXISTS chart_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    chart_type TEXT NOT NULL,          -- e.g. 'exec_summary'
    name TEXT NOT NULL DEFAULT '',     -- user label e.g. '2026 Casualty Final'
    data TEXT NOT NULL DEFAULT '{}',   -- JSON blob of chart data
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_chart_snapshots_client
    ON chart_snapshots(client_id, chart_type);
