-- Migration 074: Junction table for many-to-many requirement ↔ policy links
-- Supports manual association of policies, programs, and child-policies to requirements

CREATE TABLE IF NOT EXISTS requirement_policy_links (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_id  INTEGER NOT NULL REFERENCES coverage_requirements(id) ON DELETE CASCADE,
    policy_uid      TEXT NOT NULL,
    link_type       TEXT NOT NULL DEFAULT 'direct',   -- 'direct' | 'program' | 'child'
    is_primary      INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_rpl_req_pol ON requirement_policy_links(requirement_id, policy_uid);
CREATE INDEX IF NOT EXISTS idx_rpl_policy ON requirement_policy_links(policy_uid);

-- Migrate existing single-link data from coverage_requirements.linked_policy_uid
INSERT OR IGNORE INTO requirement_policy_links (requirement_id, policy_uid, link_type, is_primary)
SELECT id, linked_policy_uid, 'direct', 1
FROM coverage_requirements
WHERE linked_policy_uid IS NOT NULL AND linked_policy_uid != '';
