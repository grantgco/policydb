ALTER TABLE activity_log ADD COLUMN disposition TEXT;
ALTER TABLE activity_log ADD COLUMN thread_id INTEGER;
CREATE INDEX IF NOT EXISTS idx_activity_thread ON activity_log(thread_id);
