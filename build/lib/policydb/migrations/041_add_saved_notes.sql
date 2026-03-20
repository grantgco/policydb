-- Saved notes: pinned scratchpad entries preserved as timestamped, immutable journal entries.
-- Polymorphic: scope='client' with scope_id=client_id, or scope='policy' with scope_id=policy_uid.

CREATE TABLE IF NOT EXISTS saved_notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL,
    scope_id    TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_saved_notes_scope ON saved_notes(scope, scope_id);
CREATE INDEX IF NOT EXISTS idx_saved_notes_created ON saved_notes(created_at DESC);
