-- Carriers & wholesalers directory for loss run request automation
-- Promotes the previous config-list `carriers` entry to a first-class table
-- with loss-run email addresses. Wholesalers live in the same table with
-- type='wholesaler' so the same lookup works for direct-to-carrier and
-- direct-to-wholesaler loss run requests.
CREATE TABLE IF NOT EXISTS carriers (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    type           TEXT NOT NULL DEFAULT 'carrier',    -- 'carrier' | 'wholesaler'
    loss_run_email TEXT NOT NULL DEFAULT '',
    loss_run_cc    TEXT NOT NULL DEFAULT '',
    notes          TEXT NOT NULL DEFAULT '',
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (name COLLATE NOCASE, type)
);

CREATE INDEX IF NOT EXISTS idx_carriers_name_nocase ON carriers (name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_carriers_type ON carriers (type);

CREATE TRIGGER IF NOT EXISTS carriers_updated_at
AFTER UPDATE ON carriers
BEGIN
    UPDATE carriers SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

-- Note: the `email_templates.purpose` column is added by the Python wire in
-- db.py (migration 149 block) rather than here, with a pragma_table_info()
-- guard so the migration is safely idempotent across dev machines that may
-- have applied an earlier variant of the column out-of-band.
