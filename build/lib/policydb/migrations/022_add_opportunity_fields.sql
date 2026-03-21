ALTER TABLE policies ADD COLUMN is_opportunity INTEGER NOT NULL DEFAULT 0;
ALTER TABLE policies ADD COLUMN opportunity_status TEXT;
ALTER TABLE policies ADD COLUMN target_effective_date TEXT;
