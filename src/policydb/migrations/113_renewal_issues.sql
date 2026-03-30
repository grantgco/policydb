-- Renewal issues: standing issues auto-created per renewal/program
-- is_renewal_issue: flag distinguishing auto-created from manual issues
-- renewal_term_key: uniqueness key (policy_uid or program:{program_uid})

ALTER TABLE activity_log ADD COLUMN is_renewal_issue INTEGER NOT NULL DEFAULT 0;
ALTER TABLE activity_log ADD COLUMN renewal_term_key TEXT;

CREATE UNIQUE INDEX idx_one_open_renewal_issue
    ON activity_log (renewal_term_key)
    WHERE is_renewal_issue = 1
      AND issue_status NOT IN ('Resolved', 'Closed');
