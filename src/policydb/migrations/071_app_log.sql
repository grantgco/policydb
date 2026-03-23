-- Application log table for structured event logging (request metrics, business events, errors)
CREATE TABLE IF NOT EXISTS app_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    level       TEXT NOT NULL,
    logger_name TEXT,
    message     TEXT NOT NULL,
    method      TEXT,
    path        TEXT,
    status_code INTEGER,
    duration_ms REAL,
    extra       TEXT
);

CREATE INDEX IF NOT EXISTS idx_app_log_logged_at ON app_log(logged_at);
CREATE INDEX IF NOT EXISTS idx_app_log_level ON app_log(level);
CREATE INDEX IF NOT EXISTS idx_app_log_path ON app_log(path);
