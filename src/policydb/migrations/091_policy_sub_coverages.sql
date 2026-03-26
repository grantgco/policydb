CREATE TABLE IF NOT EXISTS policy_sub_coverages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id       INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    coverage_type   TEXT    NOT NULL,
    sort_order      INTEGER DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(policy_id, coverage_type)
);
CREATE INDEX IF NOT EXISTS idx_sub_cov_policy ON policy_sub_coverages(policy_id);
CREATE INDEX IF NOT EXISTS idx_sub_cov_type   ON policy_sub_coverages(coverage_type);
