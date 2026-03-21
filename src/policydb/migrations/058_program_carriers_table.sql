-- 058_program_carriers_table.sql
CREATE TABLE IF NOT EXISTS program_carriers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id    INTEGER NOT NULL REFERENCES policies(id) ON DELETE CASCADE,
    carrier       TEXT NOT NULL DEFAULT '',
    policy_number TEXT DEFAULT '',
    premium       REAL DEFAULT 0,
    limit_amount  REAL DEFAULT 0,
    sort_order    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_program_carriers_program ON program_carriers(program_id);

-- Migrate any existing comma-separated data (safety net)
INSERT INTO program_carriers (program_id, carrier, sort_order)
SELECT p.id, TRIM(c.value), c.key
FROM policies p, json_each('["' || REPLACE(p.program_carriers, ',', '","') || '"]') c
WHERE p.is_program = 1
  AND p.program_carriers IS NOT NULL
  AND p.program_carriers != '';
