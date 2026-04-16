-- Migration 154: message identity columns for comprehensive email crawl (Phase 3).
--
-- Two identifiers, two purposes:
--
-- `outlook_conversation_id` — the Outlook-native thread key. Lets the
-- crawler walk an entire conversation when any message matches, and
-- replaces the fragile subject-based thread dedup in _normalize_subject().
--
-- `outlook_internet_message_id` — the RFC-822 Message-ID header. Globally
-- unique per message. Protects against importing the same email twice
-- when the crawler encounters it in both Inbox and an Archive folder
-- during a full sweep. The existing `outlook_message_id` column holds
-- Outlook's internal numeric id, which is NOT stable across account
-- migrations or archive moves — internet_message_id is.
--
-- Partial indexes keep the indexes small: the vast majority of legacy
-- rows will have NULL for these fields until the Phase 3 crawl repopulates
-- them over time.

ALTER TABLE activity_log ADD COLUMN outlook_conversation_id TEXT;
ALTER TABLE activity_log ADD COLUMN outlook_internet_message_id TEXT;

ALTER TABLE inbox ADD COLUMN outlook_conversation_id TEXT;
ALTER TABLE inbox ADD COLUMN outlook_internet_message_id TEXT;

CREATE INDEX IF NOT EXISTS idx_activity_conv
    ON activity_log(outlook_conversation_id)
    WHERE outlook_conversation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_activity_msgid
    ON activity_log(outlook_internet_message_id)
    WHERE outlook_internet_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_inbox_conv
    ON inbox(outlook_conversation_id)
    WHERE outlook_conversation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_inbox_msgid
    ON inbox(outlook_internet_message_id)
    WHERE outlook_internet_message_id IS NOT NULL;
