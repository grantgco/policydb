-- Repoint policies.program_id FK from policies(id) to programs(id).
-- SQLite cannot ALTER FK constraints, so we rebuild the table.

PRAGMA foreign_keys = OFF;

-- Drop views that reference policies before the table rebuild
DROP VIEW IF EXISTS v_policy_status;
DROP VIEW IF EXISTS v_client_summary;
DROP VIEW IF EXISTS v_schedule;
DROP VIEW IF EXISTS v_tower;
DROP VIEW IF EXISTS v_renewal_pipeline;
DROP VIEW IF EXISTS v_overdue_followups;
DROP VIEW IF EXISTS v_review_queue;
DROP VIEW IF EXISTS v_review_clients;

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
    target_effective_date     TEXT,
    project_id                INTEGER  REFERENCES projects(id),
    access_point              TEXT,
    last_reviewed_at          DATETIME,
    review_cycle              TEXT     DEFAULT '1w',
    is_program                INTEGER  NOT NULL DEFAULT 0,
    program_carriers          TEXT,
    program_carrier_count     INTEGER,
    program_id                INTEGER  REFERENCES programs(id) ON DELETE SET NULL,
    is_bor                    INTEGER  DEFAULT 0,
    milestone_profile         TEXT     DEFAULT '',
    needs_investigation       INTEGER  DEFAULT 0,
    schematic_column          INTEGER,
    layer_notation            TEXT
);

INSERT INTO policies_new SELECT * FROM policies;

DROP TABLE policies;

ALTER TABLE policies_new RENAME TO policies;

-- Recreate indexes
CREATE INDEX IF NOT EXISTS idx_policies_program_id ON policies(program_id);

-- Recreate updated_at trigger
CREATE TRIGGER policies_updated_at
AFTER UPDATE ON policies
BEGIN
    UPDATE policies SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Recreate audit triggers
CREATE TRIGGER audit_policies_insert
AFTER INSERT ON policies
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, new_values)
    VALUES ('policies', NEW.policy_uid, 'INSERT', json_object(
        'policy_uid', NEW.policy_uid,
        'client_id', NEW.client_id,
        'policy_type', NEW.policy_type,
        'carrier', NEW.carrier,
        'policy_number', NEW.policy_number,
        'effective_date', NEW.effective_date,
        'expiration_date', NEW.expiration_date,
        'premium', NEW.premium,
        'limit_amount', NEW.limit_amount,
        'deductible', NEW.deductible,
        'description', NEW.description,
        'coverage_form', NEW.coverage_form,
        'layer_position', NEW.layer_position,
        'renewal_status', NEW.renewal_status,
        'commission_rate', NEW.commission_rate,
        'prior_premium', NEW.prior_premium,
        'account_exec', NEW.account_exec,
        'notes', NEW.notes,
        'project_name', NEW.project_name,
        'first_named_insured', NEW.first_named_insured,
        'is_opportunity', NEW.is_opportunity,
        'opportunity_status', NEW.opportunity_status,
        'is_program', NEW.is_program,
        'archived', NEW.archived
    ));
END;

CREATE TRIGGER audit_policies_update
AFTER UPDATE ON policies
WHEN OLD.policy_type IS NOT NEW.policy_type
   OR OLD.carrier IS NOT NEW.carrier
   OR OLD.policy_number IS NOT NEW.policy_number
   OR OLD.effective_date IS NOT NEW.effective_date
   OR OLD.expiration_date IS NOT NEW.expiration_date
   OR OLD.premium IS NOT NEW.premium
   OR OLD.limit_amount IS NOT NEW.limit_amount
   OR OLD.deductible IS NOT NEW.deductible
   OR OLD.description IS NOT NEW.description
   OR OLD.coverage_form IS NOT NEW.coverage_form
   OR OLD.layer_position IS NOT NEW.layer_position
   OR OLD.renewal_status IS NOT NEW.renewal_status
   OR OLD.commission_rate IS NOT NEW.commission_rate
   OR OLD.prior_premium IS NOT NEW.prior_premium
   OR OLD.account_exec IS NOT NEW.account_exec
   OR OLD.notes IS NOT NEW.notes
   OR OLD.project_name IS NOT NEW.project_name
   OR OLD.first_named_insured IS NOT NEW.first_named_insured
   OR OLD.is_opportunity IS NOT NEW.is_opportunity
   OR OLD.opportunity_status IS NOT NEW.opportunity_status
   OR OLD.is_program IS NOT NEW.is_program
   OR OLD.archived IS NOT NEW.archived
   OR OLD.follow_up_date IS NOT NEW.follow_up_date
   OR OLD.exposure_address IS NOT NEW.exposure_address
   OR OLD.placement_colleague IS NOT NEW.placement_colleague
   OR OLD.underwriter_name IS NOT NEW.underwriter_name
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, old_values, new_values)
    VALUES ('policies', NEW.policy_uid, 'UPDATE',
        json_object(
            'policy_uid', OLD.policy_uid,
            'client_id', OLD.client_id,
            'policy_type', OLD.policy_type,
            'carrier', OLD.carrier,
            'policy_number', OLD.policy_number,
            'effective_date', OLD.effective_date,
            'expiration_date', OLD.expiration_date,
            'premium', OLD.premium,
            'limit_amount', OLD.limit_amount,
            'deductible', OLD.deductible,
            'renewal_status', OLD.renewal_status,
            'commission_rate', OLD.commission_rate,
            'prior_premium', OLD.prior_premium,
            'account_exec', OLD.account_exec,
            'notes', OLD.notes,
            'project_name', OLD.project_name,
            'first_named_insured', OLD.first_named_insured,
            'is_opportunity', OLD.is_opportunity,
            'opportunity_status', OLD.opportunity_status,
            'is_program', OLD.is_program,
            'archived', OLD.archived,
            'follow_up_date', OLD.follow_up_date,
            'placement_colleague', OLD.placement_colleague,
            'underwriter_name', OLD.underwriter_name
        ),
        json_object(
            'policy_uid', NEW.policy_uid,
            'client_id', NEW.client_id,
            'policy_type', NEW.policy_type,
            'carrier', NEW.carrier,
            'policy_number', NEW.policy_number,
            'effective_date', NEW.effective_date,
            'expiration_date', NEW.expiration_date,
            'premium', NEW.premium,
            'limit_amount', NEW.limit_amount,
            'deductible', NEW.deductible,
            'renewal_status', NEW.renewal_status,
            'commission_rate', NEW.commission_rate,
            'prior_premium', NEW.prior_premium,
            'account_exec', NEW.account_exec,
            'notes', NEW.notes,
            'project_name', NEW.project_name,
            'first_named_insured', NEW.first_named_insured,
            'is_opportunity', NEW.is_opportunity,
            'opportunity_status', NEW.opportunity_status,
            'is_program', NEW.is_program,
            'archived', NEW.archived,
            'follow_up_date', NEW.follow_up_date,
            'placement_colleague', NEW.placement_colleague,
            'underwriter_name', NEW.underwriter_name
        )
    );
END;

CREATE TRIGGER audit_policies_delete
AFTER DELETE ON policies
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, old_values)
    VALUES ('policies', OLD.policy_uid, 'DELETE', json_object(
        'policy_uid', OLD.policy_uid,
        'client_id', OLD.client_id,
        'policy_type', OLD.policy_type,
        'carrier', OLD.carrier,
        'policy_number', OLD.policy_number,
        'effective_date', OLD.effective_date,
        'expiration_date', OLD.expiration_date,
        'premium', OLD.premium,
        'renewal_status', OLD.renewal_status,
        'archived', OLD.archived
    ));
END;

-- FK re-enabled by init_db() after views are recreated
