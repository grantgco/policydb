-- Import match memory: cross-source identity pairs
-- Remembers "POL-GL-2025-441 from AMS = POL-042 in PolicyDB"
-- so future reconciliations auto-match without scoring.

CREATE TABLE IF NOT EXISTS import_match_memory (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id     INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    source_name   TEXT NOT NULL,
    external_key  TEXT NOT NULL,
    key_type      TEXT DEFAULT 'policy_number',
    confidence    REAL DEFAULT 100.0,
    learned_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    learned_from  TEXT DEFAULT 'user',
    UNIQUE(source_name, external_key)
);

CREATE INDEX IF NOT EXISTS idx_match_memory_policy ON import_match_memory(policy_id);
CREATE INDEX IF NOT EXISTS idx_match_memory_lookup ON import_match_memory(source_name, external_key);
