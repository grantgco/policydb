-- Migration 138: Add missing indexes on high-frequency join/filter columns.
--
-- activity_log had no index on client_id or policy_id despite being the most
-- queried table in the application (v_client_summary fires three correlated
-- subqueries against it per client row on every list-page load).
--
-- policies had no index on client_id despite every major view joining on it.

-- activity_log indexes
CREATE INDEX IF NOT EXISTS idx_activity_log_client_id
    ON activity_log(client_id);

CREATE INDEX IF NOT EXISTS idx_activity_log_policy_id
    ON activity_log(policy_id);

CREATE INDEX IF NOT EXISTS idx_activity_log_date
    ON activity_log(activity_date DESC);

CREATE INDEX IF NOT EXISTS idx_activity_log_followup
    ON activity_log(follow_up_date)
    WHERE follow_up_done = 0 AND follow_up_date IS NOT NULL;

-- policies indexes
CREATE INDEX IF NOT EXISTS idx_policies_client_id
    ON policies(client_id);

CREATE INDEX IF NOT EXISTS idx_policies_expiration
    ON policies(expiration_date)
    WHERE archived = 0;

CREATE INDEX IF NOT EXISTS idx_policies_renewal
    ON policies(renewal_status, expiration_date)
    WHERE archived = 0;
