-- Bookmark management for Knowledge Base
CREATE TABLE IF NOT EXISTS kb_bookmarks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    uid           TEXT    NOT NULL UNIQUE,
    url           TEXT    NOT NULL,
    title         TEXT    NOT NULL DEFAULT '',
    description   TEXT    NOT NULL DEFAULT '',
    category      TEXT    NOT NULL DEFAULT 'General',
    tags          TEXT,
    favicon_url   TEXT,
    display_order INTEGER NOT NULL DEFAULT 0,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER IF NOT EXISTS kb_bookmarks_updated_at
AFTER UPDATE ON kb_bookmarks
BEGIN
    UPDATE kb_bookmarks SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE INDEX IF NOT EXISTS idx_kb_bookmarks_category ON kb_bookmarks(category);
