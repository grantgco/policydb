ALTER TABLE policies ADD COLUMN is_program INTEGER NOT NULL DEFAULT 0;
ALTER TABLE policies ADD COLUMN program_carriers TEXT;
ALTER TABLE policies ADD COLUMN program_carrier_count INTEGER;
