-- Knowledge Base: articles, documents, attachments, record links

CREATE TABLE IF NOT EXISTS kb_articles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT    NOT NULL UNIQUE,
    title       TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT 'General',
    content     TEXT    NOT NULL DEFAULT '',
    source      TEXT    NOT NULL DEFAULT 'authored',
    tags        TEXT,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS kb_articles_updated_at
AFTER UPDATE ON kb_articles
BEGIN
    UPDATE kb_articles SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE INDEX IF NOT EXISTS idx_kb_articles_category ON kb_articles(category);
CREATE INDEX IF NOT EXISTS idx_kb_articles_uid ON kb_articles(uid);

CREATE TABLE IF NOT EXISTS kb_documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    uid         TEXT    NOT NULL UNIQUE,
    title       TEXT    NOT NULL,
    category    TEXT    NOT NULL DEFAULT 'General',
    filename    TEXT    NOT NULL,
    file_path   TEXT    NOT NULL,
    file_size   INTEGER NOT NULL DEFAULT 0,
    mime_type   TEXT    NOT NULL DEFAULT 'application/pdf',
    description TEXT,
    tags        TEXT,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS kb_documents_updated_at
AFTER UPDATE ON kb_documents
BEGIN
    UPDATE kb_documents SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE INDEX IF NOT EXISTS idx_kb_documents_category ON kb_documents(category);
CREATE INDEX IF NOT EXISTS idx_kb_documents_uid ON kb_documents(uid);

CREATE TABLE IF NOT EXISTS kb_attachments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id  INTEGER NOT NULL REFERENCES kb_articles(id) ON DELETE CASCADE,
    document_id INTEGER NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    added_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(article_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_kb_attach_article ON kb_attachments(article_id);
CREATE INDEX IF NOT EXISTS idx_kb_attach_document ON kb_attachments(document_id);

CREATE TABLE IF NOT EXISTS kb_record_links (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_type  TEXT    NOT NULL,
    entry_id    INTEGER NOT NULL,
    entity_type TEXT    NOT NULL,
    entity_id   INTEGER NOT NULL,
    linked_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entry_type, entry_id, entity_type, entity_id)
);

CREATE INDEX IF NOT EXISTS idx_kb_links_entry ON kb_record_links(entry_type, entry_id);
CREATE INDEX IF NOT EXISTS idx_kb_links_entity ON kb_record_links(entity_type, entity_id);
