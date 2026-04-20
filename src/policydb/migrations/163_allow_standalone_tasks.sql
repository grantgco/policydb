-- Migration 163: allow standalone tasks (activity_log rows with no client link).
--
-- SQLite cannot drop NOT NULL in place, so rebuild activity_log with the new
-- constraint. All other columns, indexes, and audit triggers are preserved.
-- Foreign keys are left intact by the rebuild.
--
-- Views and audit triggers that reference activity_log must be dropped
-- before the rebuild (they would otherwise dangle when the original table
-- is dropped). Views are re-created by _create_views() at the end of
-- init_db(); audit triggers are re-created below because migration 067 is
-- the sole place they were originally defined.

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

-- ------------------------------------------------------------------
-- Drop views that reference activity_log. _create_views() in db.py
-- rebuilds them after init_db() finishes all migrations.
-- ------------------------------------------------------------------
DROP VIEW IF EXISTS v_policy_status;
DROP VIEW IF EXISTS v_client_summary;
DROP VIEW IF EXISTS v_renewal_pipeline;
DROP VIEW IF EXISTS v_overdue_followups;
DROP VIEW IF EXISTS v_review_queue;
DROP VIEW IF EXISTS v_review_clients;
DROP VIEW IF EXISTS v_schedule;
DROP VIEW IF EXISTS v_tower;
DROP VIEW IF EXISTS v_issue_policy_coverage;

-- ------------------------------------------------------------------
-- Drop audit triggers. They are re-created at the bottom of this
-- migration because the rebuild orphans them otherwise.
-- ------------------------------------------------------------------
DROP TRIGGER IF EXISTS audit_activity_log_insert;
DROP TRIGGER IF EXISTS audit_activity_log_update;
DROP TRIGGER IF EXISTS audit_activity_log_delete;

-- ------------------------------------------------------------------
-- Rebuild activity_log with client_id NULL-allowed.
-- Column list mirrors the live schema as of migration 162.
-- ------------------------------------------------------------------
CREATE TABLE activity_log_new (
    id                            INTEGER  PRIMARY KEY AUTOINCREMENT,
    activity_date                 DATE     NOT NULL DEFAULT CURRENT_DATE,
    client_id                     INTEGER  REFERENCES clients(id),  -- was NOT NULL
    policy_id                     INTEGER  REFERENCES policies(id),
    activity_type                 TEXT     NOT NULL,
    contact_person                TEXT,
    subject                       TEXT     NOT NULL,
    details                       TEXT,
    follow_up_date                DATE,
    follow_up_done                BOOLEAN  NOT NULL DEFAULT 0,
    account_exec                  TEXT     NOT NULL DEFAULT 'Grant',
    created_at                    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    duration_minutes              INTEGER,
    duration_hours                REAL,
    contact_id                    INTEGER  REFERENCES contacts(id),
    disposition                   TEXT,
    thread_id                     INTEGER,
    project_id                    INTEGER  REFERENCES projects(id),
    item_kind                     TEXT     DEFAULT 'followup',
    issue_id                      INTEGER  REFERENCES activity_log(id),
    issue_status                  TEXT,
    issue_severity                TEXT,
    issue_sla_days                INTEGER,
    resolution_type               TEXT,
    resolution_notes              TEXT,
    root_cause_category           TEXT,
    resolved_date                 TEXT,
    program_id                    INTEGER,
    issue_uid                     TEXT,
    is_renewal_issue              INTEGER  NOT NULL DEFAULT 0,
    renewal_term_key              TEXT,
    merged_into_id                INTEGER  REFERENCES activity_log(id),
    due_date                      TEXT,
    auto_close_reason             TEXT,
    auto_closed_at                TEXT,
    auto_closed_by                TEXT,
    merged_from_issue_id          INTEGER  REFERENCES activity_log(id),
    outlook_message_id            TEXT,
    source                        TEXT     NOT NULL DEFAULT 'manual',
    email_snippet                 TEXT,
    email_from                    TEXT,
    email_to                      TEXT,
    email_direction               TEXT,
    recurring_event_id            INTEGER  REFERENCES recurring_events(id) ON DELETE SET NULL,
    recurring_instance_date       DATE,
    outlook_conversation_id       TEXT,
    outlook_internet_message_id   TEXT,
    reviewed_at                   TEXT
);

INSERT INTO activity_log_new SELECT * FROM activity_log;

DROP TABLE activity_log;
ALTER TABLE activity_log_new RENAME TO activity_log;

-- ------------------------------------------------------------------
-- Restore every index that existed on activity_log pre-rebuild.
-- ------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_activity_thread
    ON activity_log(thread_id);

CREATE INDEX IF NOT EXISTS idx_activity_log_project_id
    ON activity_log(project_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_renewal_issue
    ON activity_log (renewal_term_key)
    WHERE is_renewal_issue = 1
      AND issue_status NOT IN ('Resolved', 'Closed');

CREATE INDEX IF NOT EXISTS idx_activity_outlook_msgid
    ON activity_log(outlook_message_id)
    WHERE outlook_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_activity_log_client_id
    ON activity_log(client_id);

CREATE INDEX IF NOT EXISTS idx_activity_log_policy_id
    ON activity_log(policy_id);

CREATE INDEX IF NOT EXISTS idx_activity_log_date
    ON activity_log(activity_date DESC);

CREATE INDEX IF NOT EXISTS idx_activity_log_followup
    ON activity_log(follow_up_date)
    WHERE follow_up_done = 0 AND follow_up_date IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_activity_log_recurring
    ON activity_log(recurring_event_id, recurring_instance_date)
    WHERE recurring_event_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_activity_conv
    ON activity_log(outlook_conversation_id)
    WHERE outlook_conversation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_activity_msgid
    ON activity_log(outlook_internet_message_id)
    WHERE outlook_internet_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_activity_log_reviewed_at
    ON activity_log (reviewed_at)
    WHERE reviewed_at IS NULL;

-- ------------------------------------------------------------------
-- Re-create the three audit triggers exactly as migration 067 defined
-- them. NULL-allowed client_id does not change the trigger bodies —
-- json_object() handles NULL values natively.
-- ------------------------------------------------------------------
CREATE TRIGGER audit_activity_log_insert
AFTER INSERT ON activity_log
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, new_values)
    VALUES ('activity_log', CAST(NEW.id AS TEXT), 'INSERT', json_object(
        'activity_date', NEW.activity_date,
        'client_id', NEW.client_id,
        'policy_id', NEW.policy_id,
        'activity_type', NEW.activity_type,
        'contact_person', NEW.contact_person,
        'subject', NEW.subject,
        'details', NEW.details,
        'follow_up_date', NEW.follow_up_date,
        'follow_up_done', NEW.follow_up_done,
        'account_exec', NEW.account_exec,
        'duration_hours', NEW.duration_hours,
        'disposition', NEW.disposition,
        'thread_id', NEW.thread_id
    ));
END;

CREATE TRIGGER audit_activity_log_update
AFTER UPDATE ON activity_log
WHEN OLD.activity_date IS NOT NEW.activity_date
   OR OLD.activity_type IS NOT NEW.activity_type
   OR OLD.contact_person IS NOT NEW.contact_person
   OR OLD.subject IS NOT NEW.subject
   OR OLD.details IS NOT NEW.details
   OR OLD.follow_up_date IS NOT NEW.follow_up_date
   OR OLD.follow_up_done IS NOT NEW.follow_up_done
   OR OLD.disposition IS NOT NEW.disposition
   OR OLD.duration_hours IS NOT NEW.duration_hours
   OR OLD.thread_id IS NOT NEW.thread_id
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, old_values, new_values)
    VALUES ('activity_log', CAST(NEW.id AS TEXT), 'UPDATE',
        json_object(
            'activity_date', OLD.activity_date,
            'client_id', OLD.client_id,
            'policy_id', OLD.policy_id,
            'activity_type', OLD.activity_type,
            'contact_person', OLD.contact_person,
            'subject', OLD.subject,
            'details', OLD.details,
            'follow_up_date', OLD.follow_up_date,
            'follow_up_done', OLD.follow_up_done,
            'disposition', OLD.disposition,
            'duration_hours', OLD.duration_hours,
            'thread_id', OLD.thread_id
        ),
        json_object(
            'activity_date', NEW.activity_date,
            'client_id', NEW.client_id,
            'policy_id', NEW.policy_id,
            'activity_type', NEW.activity_type,
            'contact_person', NEW.contact_person,
            'subject', NEW.subject,
            'details', NEW.details,
            'follow_up_date', NEW.follow_up_date,
            'follow_up_done', NEW.follow_up_done,
            'disposition', NEW.disposition,
            'duration_hours', NEW.duration_hours,
            'thread_id', NEW.thread_id
        )
    );
END;

CREATE TRIGGER audit_activity_log_delete
AFTER DELETE ON activity_log
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, old_values)
    VALUES ('activity_log', CAST(OLD.id AS TEXT), 'DELETE', json_object(
        'activity_date', OLD.activity_date,
        'client_id', OLD.client_id,
        'activity_type', OLD.activity_type,
        'subject', OLD.subject,
        'follow_up_date', OLD.follow_up_date,
        'follow_up_done', OLD.follow_up_done
    ));
END;

COMMIT;

PRAGMA foreign_keys = ON;
