-- One-time migration: convert saved_notes into activity_log entries.
-- After this, saved_notes are no longer displayed in the UI.
-- Scratchpads become working documents; "Log as Activity" is the only commit path.

-- Client-scoped saved notes → activity_log
INSERT INTO activity_log (activity_date, client_id, activity_type, subject, details, account_exec, duration_hours, created_at)
SELECT
    DATE(sn.created_at) AS activity_date,
    CAST(sn.scope_id AS INTEGER) AS client_id,
    'Note' AS activity_type,
    'Saved note' AS subject,
    sn.content AS details,
    'Grant' AS account_exec,
    0 AS duration_hours,
    sn.created_at
FROM saved_notes sn
WHERE sn.scope = 'client'
  AND sn.scope_id IS NOT NULL
  AND CAST(sn.scope_id AS INTEGER) > 0;

-- Policy-scoped saved notes → activity_log (join to get client_id)
INSERT INTO activity_log (activity_date, client_id, policy_id, activity_type, subject, details, account_exec, duration_hours, created_at)
SELECT
    DATE(sn.created_at) AS activity_date,
    p.client_id,
    p.id AS policy_id,
    'Note' AS activity_type,
    'Saved note — ' || p.policy_type AS subject,
    sn.content AS details,
    'Grant' AS account_exec,
    0 AS duration_hours,
    sn.created_at
FROM saved_notes sn
JOIN policies p ON p.policy_uid = sn.scope_id
WHERE sn.scope = 'policy';
