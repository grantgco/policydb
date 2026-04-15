-- Per-issue working notes (scratchpad) with auto-save support.
-- Mirrors client_scratchpad (014), policy_scratchpad (040), and
-- project_scratchpad (143). Issue header rows live on activity_log,
-- so this FK references activity_log.id.

CREATE TABLE IF NOT EXISTS issue_scratchpad (
    issue_id   INTEGER PRIMARY KEY REFERENCES activity_log(id) ON DELETE CASCADE,
    content    TEXT NOT NULL DEFAULT '',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS issue_scratchpad_updated
    AFTER UPDATE ON issue_scratchpad
BEGIN
    UPDATE issue_scratchpad SET updated_at = CURRENT_TIMESTAMP
    WHERE issue_id = NEW.issue_id;
END;
