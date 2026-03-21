-- Per-policy working notes (scratchpad) with auto-save support.
-- Mirrors the client_scratchpad pattern.

CREATE TABLE IF NOT EXISTS policy_scratchpad (
    policy_uid TEXT PRIMARY KEY REFERENCES policies(policy_uid) ON DELETE CASCADE,
    content    TEXT NOT NULL DEFAULT '',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS policy_scratchpad_updated
    AFTER UPDATE ON policy_scratchpad
BEGIN
    UPDATE policy_scratchpad SET updated_at = CURRENT_TIMESTAMP
    WHERE policy_uid = NEW.policy_uid;
END;
