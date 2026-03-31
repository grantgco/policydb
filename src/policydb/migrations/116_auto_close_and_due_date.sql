-- Migration 116: Add due_date and auto-close tracking columns to activity_log
-- due_date: concrete target date for issues (auto-set from expiration for renewal issues)
-- auto_close_reason: why an item was auto-closed (superseded, renewal_bound, milestone_completed, stale)
-- auto_closed_at: when the automation ran
-- auto_closed_by: which function/trigger did it (for audit trail)

ALTER TABLE activity_log ADD COLUMN due_date TEXT;
ALTER TABLE activity_log ADD COLUMN auto_close_reason TEXT;
ALTER TABLE activity_log ADD COLUMN auto_closed_at TEXT;
ALTER TABLE activity_log ADD COLUMN auto_closed_by TEXT;

-- Backfill due_date on existing renewal issues from their linked policy expiration_date
-- (standalone policies: renewal_term_key = policy_uid)
UPDATE activity_log
SET due_date = (
    SELECT p.expiration_date FROM policies p
    WHERE p.policy_uid = activity_log.renewal_term_key
)
WHERE is_renewal_issue = 1
  AND due_date IS NULL
  AND renewal_term_key IS NOT NULL
  AND renewal_term_key NOT LIKE 'program:%';

-- Backfill due_date on existing program-level renewal issues
-- (programs: renewal_term_key = 'program:{program_uid}')
UPDATE activity_log
SET due_date = (
    SELECT MAX(p.expiration_date) FROM policies p
    JOIN programs pr ON p.program_id = pr.id
    WHERE 'program:' || pr.program_uid = activity_log.renewal_term_key
)
WHERE is_renewal_issue = 1
  AND due_date IS NULL
  AND renewal_term_key IS NOT NULL
  AND renewal_term_key LIKE 'program:%';
