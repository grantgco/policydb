-- Track email direction as a first-class column instead of munging the subject
-- with a "Received: " prefix. Values: 'sent', 'received', 'flagged', or NULL.
ALTER TABLE activity_log ADD COLUMN email_direction TEXT;
ALTER TABLE inbox ADD COLUMN email_direction TEXT;

-- Backfill from the legacy "Received: " subject prefix so existing rows survive.
UPDATE activity_log
   SET email_direction = 'received'
 WHERE email_direction IS NULL
   AND activity_type = 'Email'
   AND subject LIKE 'Received: %';

UPDATE activity_log
   SET email_direction = 'sent'
 WHERE email_direction IS NULL
   AND activity_type = 'Email'
   AND outlook_message_id IS NOT NULL
   AND (subject NOT LIKE 'Received: %');

-- Strip the legacy prefix from existing subjects now that direction is tracked
-- as a column (display layer reads the column, not the prefix).
UPDATE activity_log
   SET subject = SUBSTR(subject, 11)
 WHERE subject LIKE 'Received: %';

-- Backfill inbox direction from the bracket label embedded in content
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
