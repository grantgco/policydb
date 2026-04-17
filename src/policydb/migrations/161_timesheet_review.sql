-- Phase 4: Timesheet Review
-- Adds per-activity review stamp and a week-level closeout log.

ALTER TABLE activity_log ADD COLUMN reviewed_at TEXT;

CREATE INDEX IF NOT EXISTS idx_activity_log_reviewed_at
    ON activity_log (reviewed_at)
    WHERE reviewed_at IS NULL;

CREATE TABLE IF NOT EXISTS timesheet_closeouts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start      DATE NOT NULL,
    week_end        DATE NOT NULL,
    closed_at       TEXT NOT NULL DEFAULT (datetime('now')),
    total_hours     REAL NOT NULL,
    activity_count  INTEGER NOT NULL,
    flag_count      INTEGER NOT NULL,
    UNIQUE (week_start)
);

CREATE INDEX IF NOT EXISTS idx_timesheet_closeouts_week_start
    ON timesheet_closeouts (week_start);
