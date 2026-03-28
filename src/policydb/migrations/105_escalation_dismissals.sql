-- 105_escalation_dismissals.sql
CREATE TABLE IF NOT EXISTS escalation_dismissals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id INTEGER NOT NULL,
    trigger_type TEXT NOT NULL,
    dismissed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(policy_id, trigger_type)
);
