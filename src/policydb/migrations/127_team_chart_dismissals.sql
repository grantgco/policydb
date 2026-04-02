-- 127_team_chart_dismissals.sql
-- Track permanently dismissed placement colleague suggestions on team charts
CREATE TABLE IF NOT EXISTS team_chart_dismissals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id   INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    client_id    INTEGER NOT NULL REFERENCES clients(id)  ON DELETE CASCADE,
    dismissed_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(contact_id, client_id)
);

CREATE INDEX IF NOT EXISTS idx_tcd_contact_id ON team_chart_dismissals(contact_id);
CREATE INDEX IF NOT EXISTS idx_tcd_client_id ON team_chart_dismissals(client_id);
