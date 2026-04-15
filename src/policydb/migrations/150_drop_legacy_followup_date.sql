-- Drop legacy record-level follow_up_date cache columns.
--
-- These columns existed as cached mirrors of the earliest open follow-up in
-- activity_log. The activity_log is now the sole source of truth; views and
-- queries derive "next follow-up" directly from it via grouped subqueries.
--
-- Cached data is discarded silently — anything that mattered is already in
-- activity_log because the sync helpers populated the caches from there.
--
-- The audit triggers on policies and clients reference follow_up_date in
-- their WHEN clause and json_object() calls, so they must be dropped and
-- recreated without those references before the ALTER TABLE will succeed.
-- Programs have no audit trigger.
--
-- Requires SQLite >= 3.35 for native ALTER TABLE ... DROP COLUMN.
--
-- Views that reference the columns must also be dropped first. _create_views()
-- at the end of init_db() will recreate them from the updated views.py, which
-- already derives follow_up_date from activity_log via LEFT JOIN.

DROP VIEW IF EXISTS v_policy_status;
DROP VIEW IF EXISTS v_renewal_pipeline;
DROP VIEW IF EXISTS v_review_queue;
DROP VIEW IF EXISTS v_client_summary;
DROP VIEW IF EXISTS v_review_clients;
DROP VIEW IF EXISTS v_schedule;
DROP VIEW IF EXISTS v_tower;
DROP VIEW IF EXISTS v_overdue_followups;
DROP VIEW IF EXISTS v_issue_policy_coverage;

DROP TRIGGER IF EXISTS audit_policies_insert;
DROP TRIGGER IF EXISTS audit_policies_update;
DROP TRIGGER IF EXISTS audit_policies_delete;
DROP TRIGGER IF EXISTS audit_clients_insert;
DROP TRIGGER IF EXISTS audit_clients_update;
DROP TRIGGER IF EXISTS audit_clients_delete;

ALTER TABLE policies DROP COLUMN follow_up_date;
ALTER TABLE clients DROP COLUMN follow_up_date;
ALTER TABLE programs DROP COLUMN follow_up_date;

-- Recreate audit_clients_insert without follow_up_date.
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
        'archived', NEW.archived
    ));
END;

-- Recreate audit_clients_delete (unchanged — didn't reference follow_up_date).
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

-- Recreate audit_policies_insert (unchanged — didn't reference follow_up_date).
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

-- Recreate audit_policies_delete (unchanged — didn't reference follow_up_date).
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

-- Recreate audit_clients_update without follow_up_date references.
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
            'archived', NEW.archived
        )
    );
END;

-- Recreate audit_policies_update without follow_up_date references.
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
            'placement_colleague', NEW.placement_colleague,
            'underwriter_name', NEW.underwriter_name
        )
    );
END;
