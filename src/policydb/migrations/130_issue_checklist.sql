CREATE TABLE IF NOT EXISTS issue_checklist (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id     INTEGER NOT NULL REFERENCES activity_log(id) ON DELETE CASCADE,
    label        TEXT NOT NULL,
    completed    INTEGER NOT NULL DEFAULT 0,
    completed_at DATETIME,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_issue_checklist_issue ON issue_checklist(issue_id);
