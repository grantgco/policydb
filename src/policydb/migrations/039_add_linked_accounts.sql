-- Linked accounts: group related clients (subsidiaries, sister companies, common ownership)
-- Each client can belong to at most one group (enforced by UNIQUE on client_id).

CREATE TABLE IF NOT EXISTS client_groups (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    label        TEXT,
    relationship TEXT NOT NULL DEFAULT 'Related',
    notes        TEXT,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS client_groups_updated_at
AFTER UPDATE ON client_groups
BEGIN
    UPDATE client_groups SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TABLE IF NOT EXISTS client_group_members (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id   INTEGER NOT NULL REFERENCES client_groups(id) ON DELETE CASCADE,
    client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    added_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(client_id)
);

CREATE INDEX IF NOT EXISTS idx_cgm_group ON client_group_members(group_id);
CREATE INDEX IF NOT EXISTS idx_cgm_client ON client_group_members(client_id);
