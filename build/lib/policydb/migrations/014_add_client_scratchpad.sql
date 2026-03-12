CREATE TABLE IF NOT EXISTS client_scratchpad (
    client_id  INTEGER PRIMARY KEY REFERENCES clients(id) ON DELETE CASCADE,
    content    TEXT NOT NULL DEFAULT '',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS client_scratchpad_updated
    AFTER UPDATE ON client_scratchpad
    BEGIN
        UPDATE client_scratchpad SET updated_at = CURRENT_TIMESTAMP
        WHERE client_id = NEW.client_id;
    END;
