-- Pinned notes: persistent, color-coded alerts on client/policy/project pages
CREATE TABLE IF NOT EXISTS pinned_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT    NOT NULL CHECK (scope IN ('client', 'policy', 'project')),
    scope_id    TEXT    NOT NULL,
    headline    TEXT    NOT NULL,
    detail      TEXT,
    color       TEXT    NOT NULL DEFAULT 'amber' CHECK (color IN ('red', 'amber', 'blue', 'green')),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pinned_notes_scope
    ON pinned_notes (scope, scope_id);

CREATE TRIGGER IF NOT EXISTS trg_pinned_notes_updated
    AFTER UPDATE ON pinned_notes
    FOR EACH ROW
BEGIN
    UPDATE pinned_notes SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
