-- Migration 153: outlook_folder_sync table for comprehensive email crawl (Phase 3).
--
-- Each row tracks one Outlook folder: when it was last synced, how many
-- messages have been seen, and whether the crawler should include it at
-- all. The crawler uses last_synced_at + last_message_seen to run
-- incremental syncs instead of rescanning every folder every time —
-- critical for Phase 3's "crawl all folders" model, which would be
-- unreasonably slow without per-folder state.
--
-- User-editable exclusion list (common folders like Deleted Items, Junk,
-- Drafts) lives in the `outlook_excluded_folders` config key added
-- alongside the settings UI in sub-phase 3C. The `include_in_crawl`
-- column on this table is the effective flag — computed from the
-- config list during folder discovery, then persisted so later
-- reconfiguration works without a full rediscovery.

CREATE TABLE IF NOT EXISTS outlook_folder_sync (
    folder_path TEXT PRIMARY KEY,
    folder_kind TEXT,                 -- 'inbox' | 'sent' | 'archive' | 'custom' | 'system'
    include_in_crawl INTEGER NOT NULL DEFAULT 1,
    last_synced_at TEXT,              -- ISO timestamp of last successful sync
    last_message_seen TEXT,           -- Outlook message ID of most recent message seen
    message_count INTEGER NOT NULL DEFAULT 0,
    match_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_outlook_folder_sync_include
    ON outlook_folder_sync(include_in_crawl);
