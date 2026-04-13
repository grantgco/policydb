-- 144: Recurring events — scheduled repeating touchpoints per client.
--
-- Stores cadence templates for repeating work (weekly open-items calls,
-- monthly loss-run deliverables, quarterly stewardship reports, etc.).
-- Each pending occurrence is materialized as an activity_log issue row
-- (item_kind='issue') by generate_due_recurring_instances(), inheriting
-- the full issue lifecycle (severity, SLA, resolve flow, Focus Queue).
--
-- Instances are linked back via activity_log.recurring_event_id so that
-- resolving an instance can advance the template's next_occurrence.

CREATE TABLE IF NOT EXISTS recurring_events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    recurring_uid         TEXT UNIQUE,                       -- "REC-001" via uid_sequence

    -- Scope: client required, policy optional
    client_id             INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    policy_id             INTEGER REFERENCES policies(id) ON DELETE SET NULL,

    -- Classification + content
    event_type            TEXT,                              -- from recurring_event_types config (Call/Deliverable/Meeting/Review/Report)
    name                  TEXT NOT NULL,                     -- "Weekly open items call"
    subject_template      TEXT,                              -- copied verbatim to issue.subject
    details_template      TEXT,                              -- copied verbatim to issue.details

    -- Issue defaults applied to every materialized instance
    default_severity      TEXT NOT NULL DEFAULT 'Normal',    -- resolves to issue_severity + issue_sla_days
    root_cause_default    TEXT,                              -- optional default for resolve form

    -- Recurrence rule (config-list driven; _advance() hard-codes each label)
    cadence               TEXT NOT NULL,                     -- Daily|Weekly|Biweekly|Monthly|Quarterly|Semi-Annual|Annual
    interval_n            INTEGER NOT NULL DEFAULT 1,        -- step multiplier
    day_of_week           INTEGER,                           -- 0=Mon..6=Sun (Weekly/Biweekly)
    day_of_month          INTEGER,                           -- 1-31 (Monthly/Quarterly/Semi-Annual/Annual)
    lead_days             INTEGER NOT NULL DEFAULT 0,        -- materialize N days before recurring_instance_date

    -- Window
    start_date            DATE NOT NULL,
    end_date              DATE,                              -- NULL = indefinite
    next_occurrence       DATE NOT NULL,                     -- high-water mark for generation
    last_generated_date   DATE,                              -- audit trail

    -- Defaults copied onto each instance
    account_exec          TEXT,

    -- State
    active                INTEGER NOT NULL DEFAULT 1,
    catch_up_mode         TEXT NOT NULL DEFAULT 'collapse',  -- 'collapse' | 'materialize_all'
    notes                 TEXT,

    created_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by            TEXT,
    updated_at            TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_recurring_events_client ON recurring_events(client_id);
CREATE INDEX IF NOT EXISTS idx_recurring_events_policy ON recurring_events(policy_id);
CREATE INDEX IF NOT EXISTS idx_recurring_events_next   ON recurring_events(next_occurrence) WHERE active = 1;

CREATE TRIGGER IF NOT EXISTS recurring_events_updated_at
AFTER UPDATE ON recurring_events BEGIN
    UPDATE recurring_events SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Link materialized issue instances back to their template
ALTER TABLE activity_log ADD COLUMN recurring_event_id INTEGER REFERENCES recurring_events(id) ON DELETE SET NULL;
ALTER TABLE activity_log ADD COLUMN recurring_instance_date DATE;

CREATE INDEX IF NOT EXISTS idx_activity_log_recurring
    ON activity_log(recurring_event_id, recurring_instance_date)
    WHERE recurring_event_id IS NOT NULL;

-- Seed uid_sequence with REC prefix so next_recurring_uid() can use the atomic path
INSERT INTO uid_sequence (prefix, next_val) VALUES ('REC', 0)
ON CONFLICT(prefix) DO NOTHING;
