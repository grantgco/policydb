-- Migration 159: activity_contacts junction table
-- Tags every known contact (sender + all recipients) on an activity. The
-- existing activity_log.contact_id stays as the "primary" (sender) so all
-- existing LEFT JOIN ... contacts reads continue to work unchanged; the
-- junction adds multi-party visibility.

CREATE TABLE IF NOT EXISTS activity_contacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id INTEGER NOT NULL REFERENCES activity_log(id) ON DELETE CASCADE,
    contact_id  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK(role IN ('from','to','cc','bcc')),
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(activity_id, contact_id, role)
);

CREATE INDEX IF NOT EXISTS idx_ac_activity ON activity_contacts(activity_id);
CREATE INDEX IF NOT EXISTS idx_ac_contact  ON activity_contacts(contact_id);

-- Backfill: seed the junction from every existing activity_log.contact_id
-- as role='from'. UNIQUE absorbs any re-run.
INSERT OR IGNORE INTO activity_contacts (activity_id, contact_id, role)
SELECT id, contact_id, 'from'
FROM activity_log
WHERE contact_id IS NOT NULL;
