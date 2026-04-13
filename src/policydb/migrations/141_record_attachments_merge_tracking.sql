-- Track attachment ownership when an issue is merged so dissolve can restore
-- header-level attachments back to the source issue.
ALTER TABLE record_attachments ADD COLUMN merged_from_issue_id INTEGER;

CREATE INDEX IF NOT EXISTS idx_record_attachments_merged_from
    ON record_attachments(merged_from_issue_id)
    WHERE merged_from_issue_id IS NOT NULL;
