-- Migration 152: Record renewal batches (the Renew Policies workflow successor to Bind Order).
--
-- Each row captures one invocation of execute_create_renewals for a single subject
-- (program or standalone policy). The new_uids_json column stores the list of POL-NNN
-- UIDs created by the batch so the post-submit redirect can load the Tabulator edit
-- grid filtered to just those rows.
--
-- The separate bind_events + bind_event_children tables stay — they back the
-- per-policy Mark Bound action (policy_bind.mark_policy_bound).

CREATE TABLE IF NOT EXISTS renewal_batches (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    subject_token    TEXT    NOT NULL,                    -- e.g. "program:PGM-042" or "policy:POL-017"
    new_uids_json    TEXT    NOT NULL DEFAULT '[]',       -- JSON array of new policy_uids
    excepted_count   INTEGER NOT NULL DEFAULT 0           -- count of dispositions applied (non-renewal outcomes)
);

CREATE INDEX IF NOT EXISTS idx_renewal_batches_created_at ON renewal_batches(created_at DESC);
