-- Junction table: link multiple policies to an issue
CREATE TABLE IF NOT EXISTS issue_policies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id    INTEGER NOT NULL REFERENCES activity_log(id) ON DELETE CASCADE,
    policy_id   INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(issue_id, policy_id)
);
