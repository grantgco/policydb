ALTER TABLE client_request_bundles ADD COLUMN rfi_uid TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_rfi_uid ON client_request_bundles(rfi_uid);
