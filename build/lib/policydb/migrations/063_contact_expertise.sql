-- 063_contact_expertise.sql
CREATE TABLE IF NOT EXISTS contact_expertise (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    category   TEXT NOT NULL DEFAULT 'line',
    tag        TEXT NOT NULL,
    UNIQUE(contact_id, category, tag)
);
CREATE INDEX IF NOT EXISTS idx_contact_expertise_tag ON contact_expertise(tag);
CREATE INDEX IF NOT EXISTS idx_contact_expertise_contact ON contact_expertise(contact_id);
ALTER TABLE contacts ADD COLUMN expertise_notes TEXT;
