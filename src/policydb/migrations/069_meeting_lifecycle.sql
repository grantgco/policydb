-- Add lifecycle columns to client_meetings
ALTER TABLE client_meetings ADD COLUMN meeting_type TEXT;
ALTER TABLE client_meetings ADD COLUMN phase TEXT DEFAULT 'before';
ALTER TABLE client_meetings ADD COLUMN agenda TEXT;
ALTER TABLE client_meetings ADD COLUMN start_time TEXT;
ALTER TABLE client_meetings ADD COLUMN end_time TEXT;

-- Create meeting_decisions table
CREATE TABLE IF NOT EXISTS meeting_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id INTEGER NOT NULL REFERENCES client_meetings(id),
    description TEXT NOT NULL,
    policy_uid TEXT,
    confirmed INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_meeting_decisions_meeting ON meeting_decisions(meeting_id);
