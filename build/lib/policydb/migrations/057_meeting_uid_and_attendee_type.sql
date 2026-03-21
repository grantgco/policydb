-- Add meeting_uid for tracking (like policy_uid / rfi_uid)
ALTER TABLE client_meetings ADD COLUMN meeting_uid TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_meeting_uid ON client_meetings(meeting_uid);

-- Rename is_internal to attendee_type (freeform text field)
ALTER TABLE meeting_attendees ADD COLUMN attendee_type TEXT NOT NULL DEFAULT '';

-- Fix stored CNCN-prefixed RFI UIDs from pre-fix era
UPDATE client_request_bundles
SET rfi_uid = 'CN' || SUBSTR(rfi_uid, 5)
WHERE rfi_uid LIKE 'CNCN%';
