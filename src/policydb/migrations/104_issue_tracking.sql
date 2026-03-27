-- Issue tracking columns on activity_log
-- item_kind: 'followup' (default, existing behavior) or 'issue' (new)
-- issue_id: FK to the issue header row (self-referencing activity_log)
-- issue_status: lifecycle state for issue headers (Open, Investigating, Waiting, Resolved, Closed)
-- issue_severity: priority tier (Critical, High, Normal, Low)
-- issue_sla_days: target days to resolve
-- resolution_type: how the issue was resolved
-- resolution_notes: free text wrap-up
-- root_cause_category: structured root cause for pattern detection
-- resolved_date: ISO date when resolved
-- program_id: for program-level issues (one issue per program)

ALTER TABLE activity_log ADD COLUMN issue_uid TEXT;
ALTER TABLE activity_log ADD COLUMN item_kind TEXT DEFAULT 'followup';
ALTER TABLE activity_log ADD COLUMN issue_id INTEGER REFERENCES activity_log(id);
ALTER TABLE activity_log ADD COLUMN issue_status TEXT;
ALTER TABLE activity_log ADD COLUMN issue_severity TEXT;
ALTER TABLE activity_log ADD COLUMN issue_sla_days INTEGER;
ALTER TABLE activity_log ADD COLUMN resolution_type TEXT;
ALTER TABLE activity_log ADD COLUMN resolution_notes TEXT;
ALTER TABLE activity_log ADD COLUMN root_cause_category TEXT;
ALTER TABLE activity_log ADD COLUMN resolved_date TEXT;
ALTER TABLE activity_log ADD COLUMN program_id INTEGER;
