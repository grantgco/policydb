-- Add email metadata columns to inbox for better processing UX
ALTER TABLE inbox ADD COLUMN email_subject TEXT;
ALTER TABLE inbox ADD COLUMN email_date TEXT;
ALTER TABLE inbox ADD COLUMN outlook_message_id TEXT;
