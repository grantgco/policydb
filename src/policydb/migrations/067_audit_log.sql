-- Migration 067: Database-level audit logging
-- Creates audit_log table and AFTER INSERT/UPDATE/DELETE triggers
-- on key tables to track all data changes automatically.

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name  TEXT NOT NULL,
    row_id      TEXT NOT NULL,
    operation   TEXT NOT NULL,  -- INSERT, UPDATE, DELETE
    old_values  TEXT,           -- JSON via json_object()
    new_values  TEXT,           -- JSON via json_object()
    changed_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    changed_by  TEXT             -- reserved for future use
);

CREATE INDEX IF NOT EXISTS idx_audit_log_table ON audit_log(table_name);
CREATE INDEX IF NOT EXISTS idx_audit_log_changed_at ON audit_log(changed_at);
CREATE INDEX IF NOT EXISTS idx_audit_log_operation ON audit_log(operation);

-- ============================================================
-- CLIENTS triggers
-- ============================================================

CREATE TRIGGER IF NOT EXISTS audit_clients_insert
AFTER INSERT ON clients
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, new_values)
    VALUES ('clients', CAST(NEW.id AS TEXT), 'INSERT', json_object(
        'name', NEW.name,
        'industry_segment', NEW.industry_segment,
        'primary_contact', NEW.primary_contact,
        'contact_email', NEW.contact_email,
        'contact_phone', NEW.contact_phone,
        'address', NEW.address,
        'cn_number', NEW.cn_number,
        'account_exec', NEW.account_exec,
        'date_onboarded', NEW.date_onboarded,
        'notes', NEW.notes,
        'broker_fee', NEW.broker_fee,
        'business_description', NEW.business_description,
        'website', NEW.website,
        'renewal_month', NEW.renewal_month,
        'client_since', NEW.client_since,
        'preferred_contact_method', NEW.preferred_contact_method,
        'referral_source', NEW.referral_source,
        'contact_mobile', NEW.contact_mobile,
        'is_prospect', NEW.is_prospect,
        'fein', NEW.fein,
        'hourly_rate', NEW.hourly_rate,
        'follow_up_date', NEW.follow_up_date,
        'archived', NEW.archived
    ));
END;

CREATE TRIGGER IF NOT EXISTS audit_clients_update
AFTER UPDATE ON clients
WHEN OLD.name IS NOT NEW.name
   OR OLD.industry_segment IS NOT NEW.industry_segment
   OR OLD.primary_contact IS NOT NEW.primary_contact
   OR OLD.contact_email IS NOT NEW.contact_email
   OR OLD.contact_phone IS NOT NEW.contact_phone
   OR OLD.address IS NOT NEW.address
   OR OLD.cn_number IS NOT NEW.cn_number
   OR OLD.account_exec IS NOT NEW.account_exec
   OR OLD.date_onboarded IS NOT NEW.date_onboarded
   OR OLD.notes IS NOT NEW.notes
   OR OLD.broker_fee IS NOT NEW.broker_fee
   OR OLD.business_description IS NOT NEW.business_description
   OR OLD.website IS NOT NEW.website
   OR OLD.renewal_month IS NOT NEW.renewal_month
   OR OLD.client_since IS NOT NEW.client_since
   OR OLD.preferred_contact_method IS NOT NEW.preferred_contact_method
   OR OLD.referral_source IS NOT NEW.referral_source
   OR OLD.contact_mobile IS NOT NEW.contact_mobile
   OR OLD.is_prospect IS NOT NEW.is_prospect
   OR OLD.fein IS NOT NEW.fein
   OR OLD.hourly_rate IS NOT NEW.hourly_rate
   OR OLD.follow_up_date IS NOT NEW.follow_up_date
   OR OLD.archived IS NOT NEW.archived
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, old_values, new_values)
    VALUES ('clients', CAST(NEW.id AS TEXT), 'UPDATE',
        json_object(
            'name', OLD.name,
            'industry_segment', OLD.industry_segment,
            'primary_contact', OLD.primary_contact,
            'contact_email', OLD.contact_email,
            'contact_phone', OLD.contact_phone,
            'address', OLD.address,
            'cn_number', OLD.cn_number,
            'account_exec', OLD.account_exec,
            'date_onboarded', OLD.date_onboarded,
            'notes', OLD.notes,
            'broker_fee', OLD.broker_fee,
            'business_description', OLD.business_description,
            'website', OLD.website,
            'renewal_month', OLD.renewal_month,
            'client_since', OLD.client_since,
            'preferred_contact_method', OLD.preferred_contact_method,
            'referral_source', OLD.referral_source,
            'contact_mobile', OLD.contact_mobile,
            'is_prospect', OLD.is_prospect,
            'fein', OLD.fein,
            'hourly_rate', OLD.hourly_rate,
            'follow_up_date', OLD.follow_up_date,
            'archived', OLD.archived
        ),
        json_object(
            'name', NEW.name,
            'industry_segment', NEW.industry_segment,
            'primary_contact', NEW.primary_contact,
            'contact_email', NEW.contact_email,
            'contact_phone', NEW.contact_phone,
            'address', NEW.address,
            'cn_number', NEW.cn_number,
            'account_exec', NEW.account_exec,
            'date_onboarded', NEW.date_onboarded,
            'notes', NEW.notes,
            'broker_fee', NEW.broker_fee,
            'business_description', NEW.business_description,
            'website', NEW.website,
            'renewal_month', NEW.renewal_month,
            'client_since', NEW.client_since,
            'preferred_contact_method', NEW.preferred_contact_method,
            'referral_source', NEW.referral_source,
            'contact_mobile', NEW.contact_mobile,
            'is_prospect', NEW.is_prospect,
            'fein', NEW.fein,
            'hourly_rate', NEW.hourly_rate,
            'follow_up_date', NEW.follow_up_date,
            'archived', NEW.archived
        )
    );
END;

CREATE TRIGGER IF NOT EXISTS audit_clients_delete
AFTER DELETE ON clients
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, old_values)
    VALUES ('clients', CAST(OLD.id AS TEXT), 'DELETE', json_object(
        'name', OLD.name,
        'industry_segment', OLD.industry_segment,
        'primary_contact', OLD.primary_contact,
        'contact_email', OLD.contact_email,
        'contact_phone', OLD.contact_phone,
        'address', OLD.address,
        'cn_number', OLD.cn_number,
        'account_exec', OLD.account_exec,
        'notes', OLD.notes,
        'archived', OLD.archived
    ));
END;

-- ============================================================
-- POLICIES triggers
-- ============================================================

CREATE TRIGGER IF NOT EXISTS audit_policies_insert
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

CREATE TRIGGER IF NOT EXISTS audit_policies_update
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

CREATE TRIGGER IF NOT EXISTS audit_policies_delete
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

-- ============================================================
-- ACTIVITY_LOG triggers
-- ============================================================

CREATE TRIGGER IF NOT EXISTS audit_activity_log_insert
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

CREATE TRIGGER IF NOT EXISTS audit_activity_log_update
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

CREATE TRIGGER IF NOT EXISTS audit_activity_log_delete
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

-- ============================================================
-- CONTACTS triggers
-- ============================================================

CREATE TRIGGER IF NOT EXISTS audit_contacts_insert
AFTER INSERT ON contacts
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, new_values)
    VALUES ('contacts', CAST(NEW.id AS TEXT), 'INSERT', json_object(
        'name', NEW.name,
        'email', NEW.email,
        'phone', NEW.phone,
        'mobile', NEW.mobile,
        'organization', NEW.organization
    ));
END;

CREATE TRIGGER IF NOT EXISTS audit_contacts_update
AFTER UPDATE ON contacts
WHEN OLD.name IS NOT NEW.name
   OR OLD.email IS NOT NEW.email
   OR OLD.phone IS NOT NEW.phone
   OR OLD.mobile IS NOT NEW.mobile
   OR OLD.organization IS NOT NEW.organization
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, old_values, new_values)
    VALUES ('contacts', CAST(NEW.id AS TEXT), 'UPDATE',
        json_object(
            'name', OLD.name,
            'email', OLD.email,
            'phone', OLD.phone,
            'mobile', OLD.mobile,
            'organization', OLD.organization
        ),
        json_object(
            'name', NEW.name,
            'email', NEW.email,
            'phone', NEW.phone,
            'mobile', NEW.mobile,
            'organization', NEW.organization
        )
    );
END;

CREATE TRIGGER IF NOT EXISTS audit_contacts_delete
AFTER DELETE ON contacts
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, old_values)
    VALUES ('contacts', CAST(OLD.id AS TEXT), 'DELETE', json_object(
        'name', OLD.name,
        'email', OLD.email,
        'phone', OLD.phone,
        'mobile', OLD.mobile,
        'organization', OLD.organization
    ));
END;

-- ============================================================
-- INBOX triggers
-- ============================================================

CREATE TRIGGER IF NOT EXISTS audit_inbox_insert
AFTER INSERT ON inbox
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, new_values)
    VALUES ('inbox', NEW.inbox_uid, 'INSERT', json_object(
        'inbox_uid', NEW.inbox_uid,
        'content', NEW.content,
        'client_id', NEW.client_id,
        'contact_id', NEW.contact_id,
        'status', NEW.status
    ));
END;

CREATE TRIGGER IF NOT EXISTS audit_inbox_update
AFTER UPDATE ON inbox
WHEN OLD.content IS NOT NEW.content
   OR OLD.client_id IS NOT NEW.client_id
   OR OLD.contact_id IS NOT NEW.contact_id
   OR OLD.status IS NOT NEW.status
   OR OLD.activity_id IS NOT NEW.activity_id
   OR OLD.processed_at IS NOT NEW.processed_at
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, old_values, new_values)
    VALUES ('inbox', NEW.inbox_uid, 'UPDATE',
        json_object(
            'inbox_uid', OLD.inbox_uid,
            'content', OLD.content,
            'client_id', OLD.client_id,
            'contact_id', OLD.contact_id,
            'status', OLD.status,
            'activity_id', OLD.activity_id,
            'processed_at', OLD.processed_at
        ),
        json_object(
            'inbox_uid', NEW.inbox_uid,
            'content', NEW.content,
            'client_id', NEW.client_id,
            'contact_id', NEW.contact_id,
            'status', NEW.status,
            'activity_id', NEW.activity_id,
            'processed_at', NEW.processed_at
        )
    );
END;

CREATE TRIGGER IF NOT EXISTS audit_inbox_delete
AFTER DELETE ON inbox
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, old_values)
    VALUES ('inbox', OLD.inbox_uid, 'DELETE', json_object(
        'inbox_uid', OLD.inbox_uid,
        'content', OLD.content,
        'client_id', OLD.client_id,
        'status', OLD.status
    ));
END;

-- ============================================================
-- POLICY_MILESTONES triggers
-- ============================================================

CREATE TRIGGER IF NOT EXISTS audit_policy_milestones_insert
AFTER INSERT ON policy_milestones
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, new_values)
    VALUES ('policy_milestones', CAST(NEW.id AS TEXT), 'INSERT', json_object(
        'policy_uid', NEW.policy_uid,
        'milestone', NEW.milestone,
        'completed', NEW.completed,
        'completed_at', NEW.completed_at
    ));
END;

CREATE TRIGGER IF NOT EXISTS audit_policy_milestones_update
AFTER UPDATE ON policy_milestones
WHEN OLD.milestone IS NOT NEW.milestone
   OR OLD.completed IS NOT NEW.completed
   OR OLD.completed_at IS NOT NEW.completed_at
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, old_values, new_values)
    VALUES ('policy_milestones', CAST(NEW.id AS TEXT), 'UPDATE',
        json_object(
            'policy_uid', OLD.policy_uid,
            'milestone', OLD.milestone,
            'completed', OLD.completed,
            'completed_at', OLD.completed_at
        ),
        json_object(
            'policy_uid', NEW.policy_uid,
            'milestone', NEW.milestone,
            'completed', NEW.completed,
            'completed_at', NEW.completed_at
        )
    );
END;

CREATE TRIGGER IF NOT EXISTS audit_policy_milestones_delete
AFTER DELETE ON policy_milestones
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, old_values)
    VALUES ('policy_milestones', CAST(OLD.id AS TEXT), 'DELETE', json_object(
        'policy_uid', OLD.policy_uid,
        'milestone', OLD.milestone,
        'completed', OLD.completed
    ));
END;

-- ============================================================
-- SAVED_NOTES triggers
-- ============================================================

CREATE TRIGGER IF NOT EXISTS audit_saved_notes_insert
AFTER INSERT ON saved_notes
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, new_values)
    VALUES ('saved_notes', CAST(NEW.id AS TEXT), 'INSERT', json_object(
        'scope', NEW.scope,
        'scope_id', NEW.scope_id,
        'content', NEW.content
    ));
END;

CREATE TRIGGER IF NOT EXISTS audit_saved_notes_update
AFTER UPDATE ON saved_notes
WHEN OLD.content IS NOT NEW.content
   OR OLD.scope IS NOT NEW.scope
   OR OLD.scope_id IS NOT NEW.scope_id
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, old_values, new_values)
    VALUES ('saved_notes', CAST(NEW.id AS TEXT), 'UPDATE',
        json_object(
            'scope', OLD.scope,
            'scope_id', OLD.scope_id,
            'content', OLD.content
        ),
        json_object(
            'scope', NEW.scope,
            'scope_id', NEW.scope_id,
            'content', NEW.content
        )
    );
END;

CREATE TRIGGER IF NOT EXISTS audit_saved_notes_delete
AFTER DELETE ON saved_notes
BEGIN
    INSERT INTO audit_log (table_name, row_id, operation, old_values)
    VALUES ('saved_notes', CAST(OLD.id AS TEXT), 'DELETE', json_object(
        'scope', OLD.scope,
        'scope_id', OLD.scope_id,
        'content', OLD.content
    ));
END;
