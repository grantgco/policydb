-- 076_status_manual_override.sql
-- Tracks whether compliance_status was manually set (Confirm Compliant, Waived, N/A).
-- Auto-compute skips rows with this flag set. Cleared on policy link changes.
ALTER TABLE coverage_requirements ADD COLUMN status_manual_override INTEGER DEFAULT 0;
