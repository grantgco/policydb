CREATE TABLE IF NOT EXISTS policy_timeline (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_uid      TEXT NOT NULL REFERENCES policies(policy_uid) ON DELETE CASCADE,
    milestone_name  TEXT NOT NULL,
    ideal_date      DATE NOT NULL,
    projected_date  DATE NOT NULL,
    completed_date  DATE,
    prep_alert_date DATE,
    accountability  TEXT NOT NULL DEFAULT 'my_action',
    waiting_on      TEXT,
    health          TEXT NOT NULL DEFAULT 'on_track',
    acknowledged    INTEGER NOT NULL DEFAULT 0,
    acknowledged_at DATETIME,
    created_at      DATETIME DEFAULT (datetime('now')),
    UNIQUE(policy_uid, milestone_name)
);

ALTER TABLE policies ADD COLUMN milestone_profile TEXT DEFAULT '';
