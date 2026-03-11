-- Migration 005: add exposure_unit to policies
-- Captures the rating unit (e.g. "per $100", "per $1,000", "per unit", "per sq ft")
ALTER TABLE policies ADD COLUMN exposure_unit TEXT;
