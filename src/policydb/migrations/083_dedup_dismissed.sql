-- Migration 083: Dedup dismissed pairs table
CREATE TABLE IF NOT EXISTS dedup_dismissed (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    policy_uid_a TEXT NOT NULL,
    policy_uid_b TEXT NOT NULL,
    dismissed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(client_id, policy_uid_a, policy_uid_b)
);
