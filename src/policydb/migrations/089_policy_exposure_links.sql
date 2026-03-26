-- Add denominator column to client_exposures
ALTER TABLE client_exposures ADD COLUMN denominator INTEGER NOT NULL DEFAULT 1;

-- Junction table linking policies to exposures for rate calculation
CREATE TABLE IF NOT EXISTS policy_exposure_links (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_uid      TEXT NOT NULL REFERENCES policies(policy_uid) ON DELETE CASCADE,
    exposure_id     INTEGER NOT NULL REFERENCES client_exposures(id) ON DELETE CASCADE,
    is_primary      INTEGER NOT NULL DEFAULT 0,
    rate            REAL,
    rate_updated_at DATETIME,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(policy_uid, exposure_id)
);

CREATE INDEX IF NOT EXISTS idx_pel_policy ON policy_exposure_links(policy_uid);
CREATE INDEX IF NOT EXISTS idx_pel_exposure ON policy_exposure_links(exposure_id);
CREATE INDEX IF NOT EXISTS idx_pel_primary ON policy_exposure_links(policy_uid, is_primary) WHERE is_primary = 1;
