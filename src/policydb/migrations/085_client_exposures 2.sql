CREATE TABLE IF NOT EXISTS client_exposures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    policy_id INTEGER REFERENCES policies(id) ON DELETE SET NULL,
    exposure_type TEXT NOT NULL,
    is_custom INTEGER NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT 'number',
    year INTEGER NOT NULL,
    amount REAL,
    source_document TEXT,
    notes TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_client_exposures_unique
    ON client_exposures(client_id, COALESCE(project_id, 0), exposure_type, year);

CREATE INDEX IF NOT EXISTS idx_client_exposures_corporate
    ON client_exposures(client_id, year) WHERE project_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_client_exposures_project
    ON client_exposures(project_id, year) WHERE project_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_client_exposures_policy
    ON client_exposures(policy_id) WHERE policy_id IS NOT NULL;
