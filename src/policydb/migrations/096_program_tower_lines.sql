CREATE TABLE IF NOT EXISTS program_tower_lines (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    program_policy_id   INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    source_policy_id    INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    sub_coverage_id     INTEGER REFERENCES policy_sub_coverages(id) ON DELETE CASCADE,
    label               TEXT NOT NULL,
    include_in_tower    INTEGER NOT NULL DEFAULT 1,
    sort_order          INTEGER DEFAULT 0,
    UNIQUE(program_policy_id, source_policy_id, sub_coverage_id)
);
CREATE INDEX IF NOT EXISTS idx_ptl_program ON program_tower_lines(program_policy_id);
CREATE INDEX IF NOT EXISTS idx_ptl_source ON program_tower_lines(source_policy_id);
