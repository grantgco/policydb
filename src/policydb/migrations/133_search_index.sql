-- FTS5 full-text search index for global search
-- Rebuilt from scratch on every server startup (like views).
-- Uses porter stemmer + unicode normalization + diacritic folding.
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    entity_type,
    entity_id,
    title,
    subtitle,
    body,
    metadata,
    tokenize='porter unicode61 remove_diacritics 2'
);
