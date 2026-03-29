-- Add review_override_reason to policies and programs for guided review gate
ALTER TABLE policies ADD COLUMN review_override_reason TEXT;
ALTER TABLE programs ADD COLUMN review_override_reason TEXT;
