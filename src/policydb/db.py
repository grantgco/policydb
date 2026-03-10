"""Database connection, schema initialization, and migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path


DB_DIR = Path.home() / ".policydb"
DB_PATH = DB_DIR / "policydb.sqlite"
EXPORTS_DIR = DB_DIR / "exports"
CONFIG_PATH = DB_DIR / "config.yaml"

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def get_db_path() -> Path:
    return DB_PATH


def ensure_dirs() -> None:
    DB_DIR.mkdir(exist_ok=True)
    EXPORTS_DIR.mkdir(exist_ok=True)


def get_connection(path: Path | None = None) -> sqlite3.Connection:
    """Return a configured SQLite connection with row_factory."""
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    try:
        rows = conn.execute("SELECT version FROM schema_version").fetchall()
        return {r["version"] for r in rows}
    except sqlite3.OperationalError:
        return set()


def init_db(path: Path | None = None) -> None:
    """Create schema, run pending migrations, create views."""
    ensure_dirs()
    conn = get_connection(path)
    applied = _get_applied_versions(conn)

    if 1 not in applied:
        sql = (_MIGRATIONS_DIR / "001_initial.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (1, "Initial schema"),
        )
        conn.commit()

    if 2 not in applied:
        sql = (_MIGRATIONS_DIR / "002_add_project_name.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (2, "Add project_name to policies"),
        )
        conn.commit()

    if 3 not in applied:
        sql = (_MIGRATIONS_DIR / "003_add_cn_number.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (3, "Add cn_number to clients"),
        )
        conn.commit()

    _create_views(conn)
    conn.commit()
    conn.close()


def _create_views(conn: sqlite3.Connection) -> None:
    from policydb.views import ALL_VIEWS

    for view_name, view_sql in ALL_VIEWS.items():
        conn.execute(f"DROP VIEW IF EXISTS {view_name}")
        conn.execute(view_sql)


def next_policy_uid(conn: sqlite3.Connection) -> str:
    """Generate next POL-NNN uid."""
    row = conn.execute(
        "SELECT policy_uid FROM policies ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return "POL-001"
    last = row["policy_uid"]  # e.g. "POL-042"
    try:
        n = int(last.split("-")[1]) + 1
    except (IndexError, ValueError):
        n = 1
    return f"POL-{n:03d}"
