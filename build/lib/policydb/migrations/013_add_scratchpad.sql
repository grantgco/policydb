CREATE TABLE IF NOT EXISTS user_notes (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    content    TEXT NOT NULL DEFAULT '',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO user_notes (id, content) VALUES (1, '');

CREATE TRIGGER IF NOT EXISTS user_notes_updated
    AFTER UPDATE ON user_notes
    BEGIN
        UPDATE user_notes SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
    END;
