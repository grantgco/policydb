-- Client risks / exposure tracking table
CREATE TABLE IF NOT EXISTS client_risks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id),
    category    TEXT    NOT NULL,
    description TEXT,
    severity    TEXT    NOT NULL DEFAULT 'Medium',
    has_coverage INTEGER NOT NULL DEFAULT 0,
    policy_uid  TEXT,
    notes       TEXT,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS client_risks_updated_at
AFTER UPDATE ON client_risks
BEGIN
    UPDATE client_risks SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
