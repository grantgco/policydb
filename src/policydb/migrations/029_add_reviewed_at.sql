-- Add review tracking fields to policies and clients
ALTER TABLE policies ADD COLUMN last_reviewed_at DATETIME;
ALTER TABLE policies ADD COLUMN review_cycle TEXT DEFAULT '1w';

ALTER TABLE clients ADD COLUMN last_reviewed_at DATETIME;
ALTER TABLE clients ADD COLUMN review_cycle TEXT DEFAULT '1w';
