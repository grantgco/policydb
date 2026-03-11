-- PolicyDB Initial Schema Migration
-- Version: 1

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ─── SCHEMA VERSION ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    description TEXT
);

-- ─── CLIENTS ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS clients (
    id               INTEGER  PRIMARY KEY AUTOINCREMENT,
    name             TEXT     NOT NULL UNIQUE,
    industry_segment TEXT     NOT NULL,
    primary_contact  TEXT,
    contact_email    TEXT,
    contact_phone    TEXT,
    address          TEXT,
    account_exec     TEXT     NOT NULL DEFAULT 'Grant',
    date_onboarded   DATE     NOT NULL DEFAULT CURRENT_DATE,
    notes            TEXT,
    archived         BOOLEAN  NOT NULL DEFAULT 0,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS clients_updated_at
AFTER UPDATE ON clients
BEGIN
    UPDATE clients SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- ─── POLICIES ───────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS policies (
    id                   INTEGER  PRIMARY KEY AUTOINCREMENT,
    policy_uid           TEXT     NOT NULL UNIQUE,
    client_id            INTEGER  NOT NULL REFERENCES clients(id),
    policy_type          TEXT     NOT NULL,
    carrier              TEXT     NOT NULL,
    policy_number        TEXT,
    effective_date       DATE     NOT NULL,
    expiration_date      DATE     NOT NULL,
    premium              REAL     NOT NULL DEFAULT 0,
    limit_amount         REAL     DEFAULT 0,
    deductible           REAL     DEFAULT 0,
    description          TEXT,
    coverage_form        TEXT,
    layer_position       TEXT     DEFAULT 'Primary',
    tower_group          TEXT,
    is_standalone        BOOLEAN  DEFAULT 0,
    placement_colleague  TEXT,
    underwriter_name     TEXT,
    underwriter_contact  TEXT,
    renewal_status       TEXT     NOT NULL DEFAULT 'Not Started',
    commission_rate      REAL     DEFAULT 0,
    prior_premium        REAL,
    account_exec         TEXT     NOT NULL DEFAULT 'Grant',
    notes                TEXT,
    archived             BOOLEAN  NOT NULL DEFAULT 0,
    created_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS policies_updated_at
AFTER UPDATE ON policies
BEGIN
    UPDATE policies SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- ─── ACTIVITY LOG ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS activity_log (
    id             INTEGER  PRIMARY KEY AUTOINCREMENT,
    activity_date  DATE     NOT NULL DEFAULT CURRENT_DATE,
    client_id      INTEGER  NOT NULL REFERENCES clients(id),
    policy_id      INTEGER  REFERENCES policies(id),
    activity_type  TEXT     NOT NULL,
    contact_person TEXT,
    subject        TEXT     NOT NULL,
    details        TEXT,
    follow_up_date DATE,
    follow_up_done BOOLEAN  NOT NULL DEFAULT 0,
    account_exec   TEXT     NOT NULL DEFAULT 'Grant',
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ─── PREMIUM HISTORY ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS premium_history (
    id             INTEGER  PRIMARY KEY AUTOINCREMENT,
    client_id      INTEGER  NOT NULL REFERENCES clients(id),
    policy_type    TEXT     NOT NULL,
    carrier        TEXT,
    term_effective DATE     NOT NULL,
    term_expiration DATE    NOT NULL,
    premium        REAL     NOT NULL,
    limit_amount   REAL,
    deductible     REAL,
    notes          TEXT,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(client_id, policy_type, term_effective)
);
