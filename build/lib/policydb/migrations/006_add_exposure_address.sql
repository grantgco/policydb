-- Migration 006: add primary exposure/location address fields to policies
ALTER TABLE policies ADD COLUMN exposure_address TEXT;   -- street line
ALTER TABLE policies ADD COLUMN exposure_city    TEXT;
ALTER TABLE policies ADD COLUMN exposure_state   TEXT;
ALTER TABLE policies ADD COLUMN exposure_zip     TEXT;
