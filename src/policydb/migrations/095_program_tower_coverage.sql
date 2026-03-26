CREATE TABLE IF NOT EXISTS program_tower_coverage (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    excess_policy_id           INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    underlying_policy_id       INTEGER REFERENCES policies(id) ON DELETE CASCADE,
    underlying_sub_coverage_id INTEGER REFERENCES policy_sub_coverages(id) ON DELETE CASCADE,
    CHECK (underlying_policy_id IS NOT NULL OR underlying_sub_coverage_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_ptc_excess ON program_tower_coverage(excess_policy_id);
CREATE INDEX IF NOT EXISTS idx_ptc_underlying ON program_tower_coverage(underlying_policy_id);
CREATE INDEX IF NOT EXISTS idx_ptc_subcov ON program_tower_coverage(underlying_sub_coverage_id);
