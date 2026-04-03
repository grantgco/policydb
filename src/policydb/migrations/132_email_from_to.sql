-- Add email_from and email_to columns for proper display in views
ALTER TABLE inbox ADD COLUMN email_from TEXT;
ALTER TABLE inbox ADD COLUMN email_to TEXT;
ALTER TABLE activity_log ADD COLUMN email_from TEXT;
ALTER TABLE activity_log ADD COLUMN email_to TEXT;
