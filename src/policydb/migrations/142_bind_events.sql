-- 142: Bind Events audit tables — first-class artifact for "client issued bind order" moments
-- Captures which subject (program or standalone policy) was bound, when, with what note,
-- and which children were touched (with their disposition).

CREATE TABLE IF NOT EXISTS bind_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bind_event_uid TEXT UNIQUE,                -- e.g., "BIND-001"
    bind_date DATE NOT NULL,
    subject_type TEXT NOT NULL CHECK (subject_type IN ('program', 'policy')),
    subject_id INTEGER NOT NULL,               -- programs.id or policies.id
    subject_uid TEXT NOT NULL,                 -- program_uid or policy_uid (denormalized)
    client_id INTEGER REFERENCES clients(id),
    bind_note TEXT,                            -- free-text capture of bind instruction
    policy_count_bound INTEGER NOT NULL DEFAULT 0,
    policy_count_excepted INTEGER NOT NULL DEFAULT 0,
    total_premium REAL,                        -- sum across bound children at time of bind
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_bind_events_subject ON bind_events(subject_type, subject_id);
CREATE INDEX IF NOT EXISTS idx_bind_events_client ON bind_events(client_id);
CREATE INDEX IF NOT EXISTS idx_bind_events_date ON bind_events(bind_date);

CREATE TABLE IF NOT EXISTS bind_event_children (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bind_event_id INTEGER NOT NULL REFERENCES bind_events(id) ON DELETE CASCADE,
    old_policy_uid TEXT,                       -- archived row if renew-and-bind path
    new_policy_uid TEXT NOT NULL,              -- the row that ended up bound (== old if no renew)
    renewed_inline INTEGER NOT NULL DEFAULT 0, -- 1 if renew_policy() was called as part of bind
    disposition TEXT NOT NULL,                 -- 'Bound' | 'Declined' | 'Lost' | 'Non-Renewed' | 'Defer'
    bound_effective_date DATE,
    bound_expiration_date DATE,
    bound_premium REAL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bind_event_children_event ON bind_event_children(bind_event_id);
CREATE INDEX IF NOT EXISTS idx_bind_event_children_new_uid ON bind_event_children(new_policy_uid);
