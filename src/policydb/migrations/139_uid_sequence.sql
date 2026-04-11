-- Migration 139: Add uid_sequence table for concurrency-safe UID generation.
--
-- The previous SELECT MAX → increment → INSERT pattern for policy/program UIDs
-- is not safe under concurrent requests: two simultaneous requests read the same
-- MAX value, both compute the same next UID, and the second INSERT fails on the
-- UNIQUE constraint. An atomic UPDATE on a sequence table eliminates the race.
--
-- Seed values are derived from the current maximums so existing records are
-- unaffected. The COALESCE ensures a fresh database starts at 1.

CREATE TABLE IF NOT EXISTS uid_sequence (
    prefix   TEXT    PRIMARY KEY,
    next_val INTEGER NOT NULL DEFAULT 1
);

-- Seed POL sequence from the highest existing policy UID number (or 0 for a
-- fresh database).  next_val stores the last-used number; the generator
-- syncs to the table maximum and then increments before returning, so the
-- first new UID after seeding will always be MAX(existing) + 1.
INSERT INTO uid_sequence (prefix, next_val)
SELECT 'POL',
    COALESCE(
        MAX(CAST(SUBSTR(policy_uid, 5) AS INTEGER)),
        0
    )
FROM policies
WHERE policy_uid LIKE 'POL-%'
ON CONFLICT(prefix) DO NOTHING;

-- Seed PGM sequence from the highest existing program UID number (or 0 for a
-- fresh database).
INSERT INTO uid_sequence (prefix, next_val)
SELECT 'PGM',
    COALESCE(
        MAX(CAST(SUBSTR(program_uid, 5) AS INTEGER)),
        0
    )
FROM programs
WHERE program_uid LIKE 'PGM-%'
ON CONFLICT(prefix) DO NOTHING;
