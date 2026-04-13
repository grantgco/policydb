-- Track email direction as a first-class column instead of munging the subject
-- with a "Received: " prefix. Values: 'sent', 'received', 'flagged', or NULL.
ALTER TABLE activity_log ADD COLUMN email_direction TEXT;
ALTER TABLE inbox ADD COLUMN email_direction TEXT;

-- ── Backfill activity_log.email_direction ────────────────────────────────
--
-- Order matters: the more authoritative signals run first so the catch-all
-- never overwrites a confident classification.

-- 1) Received — rows with the legacy "Received: " subject prefix
UPDATE activity_log
   SET email_direction = 'received'
 WHERE email_direction IS NULL
   AND activity_type = 'Email'
   AND subject LIKE 'Received: %';

-- 2) Received — thread-inherited rows. The legacy _run_thread_inheritance()
--    code did NOT add the "Received: " prefix to these, so they need an
--    explicit pass; without this they'd be misclassified as 'sent' by the
--    catch-all below (since they have outlook_message_id but no prefix).
UPDATE activity_log
   SET email_direction = 'received'
 WHERE email_direction IS NULL
   AND activity_type = 'Email'
   AND source = 'thread_inherit';

-- 3) Sent — outlook-sourced rows whose disposition is the legacy "Sent Email"
--    tag. This is the only authoritative outbound-direction signal in the
--    legacy data: _create_or_enrich_activity wrote disposition='Sent Email'
--    on sent imports only.
UPDATE activity_log
   SET email_direction = 'sent'
 WHERE email_direction IS NULL
   AND activity_type = 'Email'
   AND outlook_message_id IS NOT NULL
   AND disposition = 'Sent Email';

-- 4) Received — catch-all for any remaining outlook-sourced row. Anything
--    that has an outlook_message_id and isn't sent is, by elimination,
--    received (unflagged) imported correspondence.
UPDATE activity_log
   SET email_direction = 'received'
 WHERE email_direction IS NULL
   AND activity_type = 'Email'
   AND outlook_message_id IS NOT NULL;

-- Strip the legacy prefix from existing subjects now that direction is
-- tracked as a column. Display layer reads the column, not the prefix.
UPDATE activity_log
   SET subject = SUBSTR(subject, 11)
 WHERE subject LIKE 'Received: %';

-- ── Backfill inbox.email_direction from the [Outlook ...] bracket label ──
UPDATE inbox
   SET email_direction = 'sent'
 WHERE email_direction IS NULL
   AND content LIKE '[Outlook Sent]%';

UPDATE inbox
   SET email_direction = 'received'
 WHERE email_direction IS NULL
   AND content LIKE '[Outlook Received]%';

UPDATE inbox
   SET email_direction = 'flagged'
 WHERE email_direction IS NULL
   AND content LIKE '[Outlook Flagged]%';
