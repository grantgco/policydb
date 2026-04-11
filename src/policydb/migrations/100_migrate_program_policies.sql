-- Migration 100: Migrate is_program=1 policy rows to the programs table.
--
-- This migration is implemented in Python inside db.py init_db() due to the
-- complex data transformation requirements (creating new program rows, updating
-- FK references, and repointing child policies).  This file exists for sequence
-- completeness only so that file-based tooling does not report a gap at 100.
SELECT 1; -- no-op
