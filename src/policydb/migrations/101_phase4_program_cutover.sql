-- Phase 4: Program cutover
-- Main logic is in Python (db.py) because it needs next_policy_uid() for carrier conversion.
-- This SQL handles the simple structural changes.

-- Step C: Add program_id column to program_tower_lines for FK repoint
ALTER TABLE program_tower_lines ADD COLUMN program_id INTEGER REFERENCES programs(id) ON DELETE CASCADE;
