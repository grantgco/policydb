-- Migration 073: Suggested activities table for audit log review
-- Stores detected work sessions that have no corresponding activity_log entry.

CREATE TABLE IF NOT EXISTS suggested_activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id INTEGER NOT NULL,
    session_date DATE NOT NULL,
    session_start DATETIME NOT NULL,
    session_end DATETIME NOT NULL,
    estimated_duration_hours REAL NOT NULL,
    tables_touched TEXT,
    change_count INTEGER NOT NULL,
    policy_uids TEXT,
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    dismissed_at DATETIME,
    dismiss_expires_at DATETIME,
    logged_activity_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (client_id) REFERENCES clients(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_suggested_activities_unique
    ON suggested_activities(client_id, session_start);
CREATE INDEX IF NOT EXISTS idx_suggested_activities_status
    ON suggested_activities(status);
CREATE INDEX IF NOT EXISTS idx_suggested_activities_date
    ON suggested_activities(session_date);
CREATE INDEX IF NOT EXISTS idx_suggested_activities_client
    ON suggested_activities(client_id);
