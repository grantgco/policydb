-- Per-project/location working notes (scratchpad) with auto-save support.
-- Mirrors the client_scratchpad (migration 014) and policy_scratchpad (040) pattern.

CREATE TABLE IF NOT EXISTS project_scratchpad (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    content    TEXT NOT NULL DEFAULT '',
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS project_scratchpad_updated
    AFTER UPDATE ON project_scratchpad
BEGIN
    UPDATE project_scratchpad SET updated_at = CURRENT_TIMESTAMP
    WHERE project_id = NEW.project_id;
END;
