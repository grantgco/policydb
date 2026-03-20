-- Risk & Exposure Management redesign: add metadata columns + new junction tables

-- Data cleanup: fix 'None' string corruption in text fields
UPDATE clients SET cn_number = NULL WHERE cn_number = 'None';

-- Add new columns to client_risks
ALTER TABLE client_risks ADD COLUMN source TEXT;
ALTER TABLE client_risks ADD COLUMN review_date DATE;
ALTER TABLE client_risks ADD COLUMN identified_date DATE DEFAULT CURRENT_DATE;

-- Coverage lines junction table (risk → multiple coverage lines with adequacy)
CREATE TABLE IF NOT EXISTS risk_coverage_lines (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    risk_id       INTEGER NOT NULL REFERENCES client_risks(id) ON DELETE CASCADE,
    coverage_line TEXT NOT NULL,
    policy_uid    TEXT,
    adequacy      TEXT DEFAULT 'Needs Review',
    notes         TEXT,
    UNIQUE(risk_id, coverage_line)
);

-- Risk controls table
CREATE TABLE IF NOT EXISTS risk_controls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    risk_id       INTEGER NOT NULL REFERENCES client_risks(id) ON DELETE CASCADE,
    control_type  TEXT NOT NULL,
    description   TEXT NOT NULL,
    status        TEXT DEFAULT 'Recommended',
    responsible   TEXT,
    target_date   DATE,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS risk_controls_updated_at
AFTER UPDATE ON risk_controls
BEGIN
    UPDATE risk_controls SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;
