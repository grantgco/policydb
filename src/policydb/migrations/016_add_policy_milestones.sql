CREATE TABLE IF NOT EXISTS policy_milestones (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_uid   TEXT NOT NULL REFERENCES policies(policy_uid) ON DELETE CASCADE,
    milestone    TEXT NOT NULL,
    completed    INTEGER NOT NULL DEFAULT 0,
    completed_at DATETIME,
    UNIQUE(policy_uid, milestone)
);

CREATE INDEX IF NOT EXISTS idx_policy_milestones_uid ON policy_milestones(policy_uid);
