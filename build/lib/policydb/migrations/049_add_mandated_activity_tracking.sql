CREATE TABLE IF NOT EXISTS mandated_activity_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_uid   TEXT NOT NULL REFERENCES policies(policy_uid) ON DELETE CASCADE,
    rule_name    TEXT NOT NULL,
    activity_id  INTEGER REFERENCES activity_log(id),
    milestone_id INTEGER REFERENCES policy_milestones(id),
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(policy_uid, rule_name)
);
