-- Review session tracking for guided weekly walkthrough
CREATE TABLE IF NOT EXISTS review_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at    TEXT,
    sections_json   TEXT NOT NULL DEFAULT '{}',
    vacation_return_date TEXT
);
