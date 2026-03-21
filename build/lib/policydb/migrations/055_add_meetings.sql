CREATE TABLE IF NOT EXISTS client_meetings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id     INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    meeting_date  DATE NOT NULL DEFAULT (date('now')),
    meeting_time  TEXT,
    duration_hours REAL,
    location      TEXT,
    notes         TEXT NOT NULL DEFAULT '',
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS meeting_attendees (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  INTEGER NOT NULL REFERENCES client_meetings(id) ON DELETE CASCADE,
    contact_id  INTEGER REFERENCES contacts(id),
    name        TEXT NOT NULL,
    role        TEXT,
    is_internal INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS meeting_action_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  INTEGER NOT NULL REFERENCES client_meetings(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    assignee    TEXT,
    due_date    DATE,
    completed   INTEGER NOT NULL DEFAULT 0,
    activity_id INTEGER REFERENCES activity_log(id)
);

CREATE INDEX IF NOT EXISTS idx_meetings_client ON client_meetings(client_id);
CREATE INDEX IF NOT EXISTS idx_meetings_date ON client_meetings(meeting_date);
CREATE INDEX IF NOT EXISTS idx_meeting_attendees ON meeting_attendees(meeting_id);
CREATE INDEX IF NOT EXISTS idx_meeting_actions ON meeting_action_items(meeting_id);

CREATE TRIGGER IF NOT EXISTS client_meetings_updated_at
AFTER UPDATE ON client_meetings
BEGIN
    UPDATE client_meetings SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
