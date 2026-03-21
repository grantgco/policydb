-- Requirement sources: contracts, agreements, loan covenants
CREATE TABLE IF NOT EXISTS requirement_sources (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    project_id      INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    name            TEXT NOT NULL,
    counterparty    TEXT,
    clause_ref      TEXT,
    notes           TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS requirement_sources_updated_at
    AFTER UPDATE ON requirement_sources
    FOR EACH ROW
    BEGIN UPDATE requirement_sources SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id; END;

-- Coverage requirements: what coverage is needed
CREATE TABLE IF NOT EXISTS coverage_requirements (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id               INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    project_id              INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    risk_id                 INTEGER REFERENCES client_risks(id) ON DELETE SET NULL,
    source_id               INTEGER REFERENCES requirement_sources(id) ON DELETE SET NULL,
    coverage_line           TEXT NOT NULL,
    required_limit          REAL,
    max_deductible          REAL,
    deductible_type         TEXT,
    ai_required             INTEGER DEFAULT 0,
    wos_required            INTEGER DEFAULT 0,
    primary_noncontrib      INTEGER DEFAULT 0,
    per_project_aggregate   INTEGER DEFAULT 0,
    noc_required            INTEGER DEFAULT 0,
    completed_ops_required  INTEGER DEFAULT 0,
    professional_liability_required INTEGER DEFAULT 0,
    pollution_required      INTEGER DEFAULT 0,
    cyber_required          INTEGER DEFAULT 0,
    builders_risk_required  INTEGER DEFAULT 0,
    compliance_status       TEXT DEFAULT 'Needs Review',
    linked_policy_uid       TEXT,
    notes                   TEXT,
    created_at              DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS coverage_requirements_updated_at
    AFTER UPDATE ON coverage_requirements
    FOR EACH ROW
    BEGIN UPDATE coverage_requirements SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id; END;

-- Reusable requirement templates (global, not per-client)
CREATE TABLE IF NOT EXISTS requirement_templates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Items within a requirement template
CREATE TABLE IF NOT EXISTS requirement_template_items (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id             INTEGER NOT NULL REFERENCES requirement_templates(id) ON DELETE CASCADE,
    coverage_line           TEXT NOT NULL,
    required_limit          REAL,
    max_deductible          REAL,
    deductible_type         TEXT,
    ai_required             INTEGER DEFAULT 0,
    wos_required            INTEGER DEFAULT 0,
    primary_noncontrib      INTEGER DEFAULT 0,
    per_project_aggregate   INTEGER DEFAULT 0,
    noc_required            INTEGER DEFAULT 0,
    completed_ops_required  INTEGER DEFAULT 0,
    professional_liability_required INTEGER DEFAULT 0,
    pollution_required      INTEGER DEFAULT 0,
    cyber_required          INTEGER DEFAULT 0,
    builders_risk_required  INTEGER DEFAULT 0,
    notes                   TEXT
);

-- COPE data per location (optional, not all locations need it)
CREATE TABLE IF NOT EXISTS cope_data (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id              INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    construction_type       TEXT,
    year_built              INTEGER,
    stories                 INTEGER,
    sq_footage              REAL,
    sprinklered             TEXT DEFAULT 'Unknown',
    roof_type               TEXT,
    occupancy_description   TEXT,
    protection_class        TEXT,
    total_insurable_value   REAL,
    notes                   TEXT,
    created_at              DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER IF NOT EXISTS cope_data_updated_at
    AFTER UPDATE ON cope_data
    FOR EACH ROW
    BEGIN UPDATE cope_data SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id; END;

-- Performance indexes
CREATE INDEX IF NOT EXISTS idx_coverage_requirements_client_project
    ON coverage_requirements(client_id, project_id);
CREATE INDEX IF NOT EXISTS idx_coverage_requirements_source
    ON coverage_requirements(source_id);
CREATE INDEX IF NOT EXISTS idx_requirement_sources_client
    ON requirement_sources(client_id);
CREATE INDEX IF NOT EXISTS idx_cope_data_project
    ON cope_data(project_id);
