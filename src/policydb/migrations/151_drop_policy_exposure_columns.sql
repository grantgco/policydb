-- Migration 151: drop the deprecated policies.exposure_* columns.
--
-- Exposure rating data now lives exclusively on client_exposures +
-- policy_exposure_links (see migrations 086, 089 and the Bug 3 PR).
-- Location address lives on the projects row.  The seven columns being
-- dropped here were the last dual-write surface; all read sites were
-- rewired in this commit.
--
-- One final backfill runs before the DROPs so any straggler data still
-- sitting on the legacy columns ends up on the projects row or in
-- client_exposures before the schema change makes it unreachable.
-- (Per the planning discussion: we're okay dropping data if the backfill
-- can't find a sensible target, since the normalized tables are the
-- source of truth going forward.)

-- ─── Final backfill: straggler address data → projects ─────────────────
-- For any project that is still missing address fields, copy them from
-- the most recent linked policy's exposure_* columns as a last chance.
UPDATE projects
SET address = (
        SELECT p.exposure_address
        FROM policies p
        WHERE p.project_id = projects.id
          AND p.exposure_address IS NOT NULL
          AND p.exposure_address != ''
        ORDER BY p.id DESC
        LIMIT 1
    )
WHERE (address IS NULL OR address = '')
  AND EXISTS (
      SELECT 1 FROM policies p
      WHERE p.project_id = projects.id
        AND p.exposure_address IS NOT NULL
        AND p.exposure_address != ''
  );

UPDATE projects
SET city = (
        SELECT p.exposure_city
        FROM policies p
        WHERE p.project_id = projects.id
          AND p.exposure_city IS NOT NULL
          AND p.exposure_city != ''
        ORDER BY p.id DESC
        LIMIT 1
    )
WHERE (city IS NULL OR city = '')
  AND EXISTS (
      SELECT 1 FROM policies p
      WHERE p.project_id = projects.id
        AND p.exposure_city IS NOT NULL
        AND p.exposure_city != ''
  );

UPDATE projects
SET state = (
        SELECT p.exposure_state
        FROM policies p
        WHERE p.project_id = projects.id
          AND p.exposure_state IS NOT NULL
          AND p.exposure_state != ''
        ORDER BY p.id DESC
        LIMIT 1
    )
WHERE (state IS NULL OR state = '')
  AND EXISTS (
      SELECT 1 FROM policies p
      WHERE p.project_id = projects.id
        AND p.exposure_state IS NOT NULL
        AND p.exposure_state != ''
  );

UPDATE projects
SET zip = (
        SELECT p.exposure_zip
        FROM policies p
        WHERE p.project_id = projects.id
          AND p.exposure_zip IS NOT NULL
          AND p.exposure_zip != ''
        ORDER BY p.id DESC
        LIMIT 1
    )
WHERE (zip IS NULL OR zip = '')
  AND EXISTS (
      SELECT 1 FROM policies p
      WHERE p.project_id = projects.id
        AND p.exposure_zip IS NOT NULL
        AND p.exposure_zip != ''
  );

-- Drop the views that reference these columns so the DROP COLUMN can
-- succeed.  init_db() rebuilds every view from views.py after migrations
-- run, so this is safe.
DROP VIEW IF EXISTS v_policy_status;
DROP VIEW IF EXISTS v_schedule;
DROP VIEW IF EXISTS v_tower;
DROP VIEW IF EXISTS v_renewal_pipeline;
DROP VIEW IF EXISTS v_review_queue;

-- Drop the audit trigger that references exposure_address in its WHEN
-- clause; we recreate it below without the reference.
DROP TRIGGER IF EXISTS audit_policies_update;

-- ─── Drop the deprecated columns (SQLite 3.35+) ────────────────────────
ALTER TABLE policies DROP COLUMN exposure_basis;
ALTER TABLE policies DROP COLUMN exposure_amount;
ALTER TABLE policies DROP COLUMN exposure_unit;
ALTER TABLE policies DROP COLUMN exposure_denominator;
ALTER TABLE policies DROP COLUMN exposure_address;
ALTER TABLE policies DROP COLUMN exposure_city;
ALTER TABLE policies DROP COLUMN exposure_state;
ALTER TABLE policies DROP COLUMN exposure_zip;

-- Recreate audit_policies_update without the exposure_address WHEN clause
-- (matches the version from migration 150, minus that single line).
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
