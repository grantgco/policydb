-- Track Outlook message IDs whose activities were intentionally deleted,
-- so email sync doesn't re-import them.
CREATE TABLE IF NOT EXISTS dismissed_outlook_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id   TEXT NOT NULL,
    dismissed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(message_id)
);
