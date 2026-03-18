-- Meeting ↔ Policy junction (many-to-many)
CREATE TABLE IF NOT EXISTS meeting_policies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    meeting_id  INTEGER NOT NULL REFERENCES client_meetings(id) ON DELETE CASCADE,
    policy_uid  TEXT NOT NULL,
    UNIQUE(meeting_id, policy_uid)
);
CREATE INDEX IF NOT EXISTS idx_meeting_policies ON meeting_policies(meeting_id);

-- Action items can link to a specific policy
ALTER TABLE meeting_action_items ADD COLUMN policy_uid TEXT;
