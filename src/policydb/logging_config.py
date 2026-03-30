"""Application logging setup for PolicyDB.

Configures Python logging with two handlers:
- RotatingFileHandler → ~/.policydb/logs/policydb.log (for tail -f)
- SQLiteHandler → app_log table (for UI querying)
"""

from __future__ import annotations

import atexit
import logging
import queue
import sqlite3
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from policydb.db import DB_DIR, DB_PATH

LOGS_DIR = DB_DIR / "logs"
LOG_FILE = LOGS_DIR / "policydb.log"

_sqlite_handler: SQLiteHandler | None = None


def setup_logging() -> None:
    """Configure root logger with file + stderr handlers. Call once at startup."""
    import policydb.config as cfg

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    level_name = cfg.get("log_level", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger("policydb")
    root.setLevel(logging.DEBUG)  # Let handlers filter

    # File handler — rotated logs for tail -f debugging
    if not any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        fh = RotatingFileHandler(
            str(LOG_FILE), maxBytes=5_000_000, backupCount=5, encoding="utf-8"
        )
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter(
            "[%(asctime)s] %(levelname)-8s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(fh)

    # Stderr handler — warnings+ to console
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in root.handlers):
        sh = logging.StreamHandler()
        sh.setLevel(logging.WARNING)
        sh.setFormatter(logging.Formatter(
            "[%(levelname)s] %(name)s: %(message)s"
        ))
        root.addHandler(sh)


def setup_sqlite_handler() -> None:
    """Add the SQLite handler after the app_log table exists (post-migration).

    Call this after init_db() completes so the table is guaranteed to exist.
    """
    global _sqlite_handler

    root = logging.getLogger("policydb")
    if any(isinstance(h, SQLiteHandler) for h in root.handlers):
        return  # Already attached

    try:
        handler = SQLiteHandler(str(DB_PATH))
        handler.setLevel(logging.INFO)
        root.addHandler(handler)
        _sqlite_handler = handler
        atexit.register(_shutdown_sqlite_handler)
    except Exception as e:
        # Don't block startup, but log to stderr so it's visible
        logging.getLogger("policydb").warning("SQLite log handler setup failed: %s", e)


def _shutdown_sqlite_handler() -> None:
    if _sqlite_handler is not None:
        _sqlite_handler.close()


class SQLiteHandler(logging.Handler):
    """Logging handler that writes to the app_log SQLite table.

    Uses a background thread with a queue to avoid blocking request handlers.
    Flushes every 5 seconds or 50 entries, whichever comes first.
    """

    FLUSH_INTERVAL = 5.0  # seconds
    FLUSH_SIZE = 50  # entries

    def __init__(self, db_path: str) -> None:
        super().__init__()
        self._db_path = db_path
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._urgent = threading.Event()  # Signal immediate flush for errors
        self._thread = threading.Thread(target=self._writer, daemon=True, name="log-writer")
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = {
                "level": record.levelname,
                "logger_name": record.name,
                "message": self.format(record) if self.formatter else record.getMessage(),
                "method": getattr(record, "method", None),
                "path": getattr(record, "path", None),
                "status_code": getattr(record, "status_code", None),
                "duration_ms": getattr(record, "duration_ms", None),
                "extra": getattr(record, "log_extra", None),
            }
            self._queue.put_nowait(entry)
            # Wake the writer thread immediately for errors/warnings
            if record.levelno >= logging.WARNING:
                self._urgent.set()
        except Exception:
            self.handleError(record)

    def _writer(self) -> None:
        """Background thread that drains the queue into SQLite."""
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
        except Exception:
            return

        buffer: list[dict] = []

        while not self._stop.is_set():
            # Wait for either timeout or urgent signal
            self._urgent.wait(timeout=self.FLUSH_INTERVAL)
            self._urgent.clear()

            # Drain all queued entries
            while not self._queue.empty():
                try:
                    buffer.append(self._queue.get_nowait())
                except queue.Empty:
                    break

            if buffer:
                self._flush_buffer(conn, buffer)
                buffer = []

        # Final flush on shutdown
        while not self._queue.empty():
            try:
                buffer.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if buffer:
            self._flush_buffer(conn, buffer)

        if conn:
            conn.close()

    def _flush_buffer(self, conn: sqlite3.Connection, buffer: list[dict]) -> None:
        try:
            conn.executemany(
                "INSERT INTO app_log (level, logger_name, message, method, path, "
                "status_code, duration_ms, extra) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (e["level"], e["logger_name"], e["message"], e["method"],
                     e["path"], e["status_code"], e["duration_ms"], e["extra"])
                    for e in buffer
                ],
            )
            conn.commit()
        except Exception:
            pass  # Don't crash the writer thread

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)
        super().close()

    def flush(self) -> None:
        # Wait briefly for the writer to drain
        deadline = time.time() + 1.0
        while not self._queue.empty() and time.time() < deadline:
            time.sleep(0.05)
