-- Migration 024: Remove NOT NULL constraints from policies.effective_date,
-- policies.expiration_date, and policies.carrier so that opportunities can be
-- created without bound-policy fields.

PRAGMA foreign_keys = OFF;

-- Drop all views that reference policies so SQLite's ALTER TABLE rename
-- does not fail trying to validate them while the table is mid-swap.
DROP VIEW IF EXISTS v_policy_status;
DROP VIEW IF EXISTS v_client_summary;
DROP VIEW IF EXISTS v_schedule;
DROP VIEW IF EXISTS v_tower;
DROP VIEW IF EXISTS v_renewal_pipeline;
DROP VIEW IF EXISTS v_overdue_followups;

DROP TABLE IF EXISTS policies_new;

CREATE TABLE policies_new (
    id                        INTEGER  PRIMARY KEY AUTOINCREMENT,
    policy_uid                TEXT     NOT NULL UNIQUE,
    client_id                 INTEGER  NOT NULL REFERENCES clients(id),
    policy_type               TEXT     NOT NULL,
    carrier                   TEXT,
    policy_number             TEXT,
    effective_date            DATE,
    expiration_date           DATE,
    premium                   REAL     NOT NULL DEFAULT 0,
    limit_amount              REAL     DEFAULT 0,
    deductible                REAL     DEFAULT 0,
    description               TEXT,
    coverage_form             TEXT,
    layer_position            TEXT     DEFAULT 'Primary',
    tower_group               TEXT,
    is_standalone             BOOLEAN  DEFAULT 0,
    placement_colleague       TEXT,
    underwriter_name          TEXT,
    underwriter_contact       TEXT,
    renewal_status            TEXT     NOT NULL DEFAULT 'Not Started',
    commission_rate           REAL     DEFAULT 0,
    prior_premium             REAL,
    account_exec              TEXT     NOT NULL DEFAULT 'Grant',
    notes                     TEXT,
    archived                  BOOLEAN  NOT NULL DEFAULT 0,
    created_at                DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    project_name              TEXT,
    exposure_basis            TEXT,
    exposure_amount           REAL,
    exposure_unit             TEXT,
    exposure_address          TEXT,
    exposure_city             TEXT,
    exposure_state            TEXT,
    exposure_zip              TEXT,
    prior_policy_uid          TEXT,
    follow_up_date            DATE,
    attachment_point          REAL,
    participation_of          REAL,
    placement_colleague_email TEXT,
    first_named_insured       TEXT,
    is_opportunity            INTEGER  NOT NULL DEFAULT 0,
    opportunity_status        TEXT,
    target_effective_date     TEXT
);

INSERT INTO policies_new SELECT
    id, policy_uid, client_id, policy_type, carrier, policy_number,
    effective_date, expiration_date, premium, limit_amount, deductible,
    description, coverage_form, layer_position, tower_group, is_standalone,
    placement_colleague, underwriter_name, underwriter_contact,
    renewal_status, commission_rate, prior_premium, account_exec, notes,
    archived, created_at, updated_at, project_name, exposure_basis,
    exposure_amount, exposure_unit, exposure_address, exposure_city,
    exposure_state, exposure_zip, prior_policy_uid, follow_up_date,
    attachment_point, participation_of, placement_colleague_email,
    first_named_insured, is_opportunity, opportunity_status, target_effective_date
FROM policies;

DROP TABLE policies;

ALTER TABLE policies_new RENAME TO policies;

CREATE TRIGGER IF NOT EXISTS policies_updated_at
AFTER UPDATE ON policies
BEGIN
    UPDATE policies SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

PRAGMA foreign_keys = ON;
