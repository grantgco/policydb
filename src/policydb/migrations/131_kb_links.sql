-- Unified bi-directional linking table for Knowledge Base reference web.
-- Replaces split kb_record_links / record_attachments for KB-related links.

CREATE TABLE IF NOT EXISTS kb_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,   -- 'kb_article', 'attachment', 'issue', 'policy', 'client', 'activity', 'project'
    source_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(source_type, source_id, target_type, target_id)
);

CREATE INDEX IF NOT EXISTS idx_kb_links_source ON kb_links(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_kb_links_target ON kb_links(target_type, target_id);

-- Migrate existing kb_record_links into kb_links
INSERT OR IGNORE INTO kb_links (source_type, source_id, target_type, target_id, created_at)
SELECT entry_type, entry_id, entity_type, entity_id, linked_at
FROM kb_record_links;

-- Migrate record_attachments where record_type = 'kb_article' into kb_links
INSERT OR IGNORE INTO kb_links (source_type, source_id, target_type, target_id, created_at)
SELECT 'attachment', attachment_id, 'kb_article', record_id, created_at
FROM record_attachments
WHERE record_type = 'kb_article';
