CREATE TABLE IF NOT EXISTS project_notes (
    id           INTEGER  PRIMARY KEY AUTOINCREMENT,
    client_id    INTEGER  NOT NULL REFERENCES clients(id),
    project_name TEXT     NOT NULL,
    notes        TEXT     NOT NULL DEFAULT '',
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(client_id, project_name)
);

CREATE TRIGGER IF NOT EXISTS project_notes_updated_at
AFTER UPDATE ON project_notes
BEGIN
    UPDATE project_notes SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
