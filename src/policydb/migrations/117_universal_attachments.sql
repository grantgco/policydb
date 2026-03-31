-- Universal attachments system: replaces kb_documents with a unified
-- attachment model supporting DevonThink links and local files.
-- Polymorphic record_attachments table links any attachment to any record type.

CREATE TABLE IF NOT EXISTS attachments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT    UNIQUE NOT NULL,
    title       TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT 'local',   -- 'devonthink' or 'local'
    dt_uuid     TEXT,                                -- DevonThink item UUID
    dt_url      TEXT,                                -- x-devonthink-item:// URL
    file_path   TEXT,                                -- local disk path (null for DT-only)
    filename    TEXT    DEFAULT '',
    file_size   INTEGER DEFAULT 0,
    mime_type   TEXT    DEFAULT '',
    category    TEXT    DEFAULT 'General',
    description TEXT    DEFAULT '',
    tags        TEXT    DEFAULT '[]',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS record_attachments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    attachment_id   INTEGER NOT NULL REFERENCES attachments(id) ON DELETE CASCADE,
    record_type     TEXT    NOT NULL,  -- policy, client, activity, rfi_bundle, kb_article, meeting
    record_id       INTEGER NOT NULL,
    sort_order      INTEGER DEFAULT 0,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(attachment_id, record_type, record_id)
);

CREATE INDEX IF NOT EXISTS idx_record_attachments_record ON record_attachments(record_type, record_id);
CREATE INDEX IF NOT EXISTS idx_record_attachments_attachment ON record_attachments(attachment_id);
CREATE INDEX IF NOT EXISTS idx_attachments_source ON attachments(source);
CREATE INDEX IF NOT EXISTS idx_attachments_dt_uuid ON attachments(dt_uuid);

-- Trigger: auto-update updated_at on attachments
CREATE TRIGGER IF NOT EXISTS trg_attachments_updated
    AFTER UPDATE ON attachments
    FOR EACH ROW
BEGIN
    UPDATE attachments SET updated_at = CURRENT_TIMESTAMP WHERE id = OLD.id;
END;

-- ── Migrate kb_documents → attachments ──────────────────────────────────────
-- Preserve original KBD-xxx UIDs so existing KB document URLs keep working.
INSERT INTO attachments (id, uid, title, source, file_path, filename, file_size, mime_type, category, description, tags, created_at, updated_at)
SELECT id,
       uid,
       COALESCE(title, ''),
       'local',
       file_path,
       COALESCE(filename, ''),
       COALESCE(file_size, 0),
       COALESCE(mime_type, ''),
       COALESCE(category, 'General'),
       COALESCE(description, ''),
       COALESCE(tags, '[]'),
       created_at,
       updated_at
FROM kb_documents;

-- ── Migrate kb_attachments → record_attachments ─────────────────────────────
INSERT OR IGNORE INTO record_attachments (attachment_id, record_type, record_id, sort_order, created_at)
SELECT ka.document_id,
       'kb_article',
       ka.article_id,
       COALESCE(ka.sort_order, 0),
       COALESCE(ka.added_at, CURRENT_TIMESTAMP)
FROM kb_attachments ka
WHERE ka.document_id IN (SELECT id FROM attachments);

-- ── Migrate kb_record_links → record_attachments ────────────────────────────
-- Only for document links (entry_type = 'document')
INSERT OR IGNORE INTO record_attachments (attachment_id, record_type, record_id, created_at)
SELECT krl.entry_id,
       krl.entity_type,   -- 'client' or 'policy'
       krl.entity_id,
       COALESCE(krl.linked_at, CURRENT_TIMESTAMP)
FROM kb_record_links krl
WHERE krl.entry_type = 'document'
  AND krl.entry_id IN (SELECT id FROM attachments);

-- Also migrate article record links (keep articles linked to records)
-- These will be stored as record_type pointing to the article, not the attachment
-- We handle this in code since kb_record_links for articles stays in kb_record_links table
