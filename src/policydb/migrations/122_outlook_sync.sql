ALTER TABLE activity_log ADD COLUMN outlook_message_id TEXT;
ALTER TABLE activity_log ADD COLUMN source TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE activity_log ADD COLUMN email_snippet TEXT;
CREATE INDEX IF NOT EXISTS idx_activity_outlook_msgid ON activity_log(outlook_message_id) WHERE outlook_message_id IS NOT NULL;
