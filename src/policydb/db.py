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
    conn = sqlite3.connect(str(path or DB_PATH), check_same_thread=False)
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

    if 4 not in applied:
        sql = (_MIGRATIONS_DIR / "004_add_exposure_basis.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (4, "Add exposure_basis and exposure_amount to policies"),
        )
        conn.commit()

    if 5 not in applied:
        sql = (_MIGRATIONS_DIR / "005_add_exposure_unit.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (5, "Add exposure_unit to policies"),
        )
        conn.commit()

    if 6 not in applied:
        sql = (_MIGRATIONS_DIR / "006_add_exposure_address.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (6, "Add exposure address fields to policies"),
        )
        conn.commit()

    if 7 not in applied:
        sql = (_MIGRATIONS_DIR / "007_add_prior_policy_uid.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (7, "Add prior_policy_uid to policies for renewal term lineage"),
        )
        conn.commit()

    if 8 not in applied:
        sql = (_MIGRATIONS_DIR / "008_add_broker_fee.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (8, "Add broker_fee and business_description to clients"),
        )
        conn.commit()

    if 9 not in applied:
        sql = (_MIGRATIONS_DIR / "009_add_policy_followup_date.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (9, "Add follow_up_date to policies"),
        )
        conn.commit()

    if 10 not in applied:
        sql = (_MIGRATIONS_DIR / "010_add_tower_fields.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (10, "Add attachment_point and participation_of to policies"),
        )
        conn.commit()

    if 11 not in applied:
        sql = (_MIGRATIONS_DIR / "011_add_placement_colleague_email.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (11, "Add placement_colleague_email to policies"),
        )
        conn.commit()

    if 12 not in applied:
        sql = (_MIGRATIONS_DIR / "012_add_project_notes.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (12, "Add project_notes table"),
        )
        conn.commit()

    if 13 not in applied:
        sql = (_MIGRATIONS_DIR / "013_add_scratchpad.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (13, "Add user_notes scratchpad"),
        )
        conn.commit()

    if 14 not in applied:
        sql = (_MIGRATIONS_DIR / "014_add_client_scratchpad.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (14, "Add client_scratchpad table"),
        )
        conn.commit()

    if 15 not in applied:
        sql = (_MIGRATIONS_DIR / "015_add_client_contacts.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (15, "Add client_contacts table"),
        )
        conn.commit()

    if 16 not in applied:
        sql = (_MIGRATIONS_DIR / "016_add_policy_milestones.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (16, "Add policy_milestones table"),
        )
        conn.commit()

    if 17 not in applied:
        sql = (_MIGRATIONS_DIR / "017_add_contact_role.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (17, "Add role column to client_contacts"),
        )
        conn.commit()

    if 18 not in applied:
        sql = (_MIGRATIONS_DIR / "018_add_contact_type.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (18, "Add contact_type column to client_contacts"),
        )
        conn.commit()

    if 19 not in applied:
        sql = (_MIGRATIONS_DIR / "019_add_policy_contacts.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (19, "Add policy_contacts table"),
        )
        conn.commit()

    if 20 not in applied:
        sql = (_MIGRATIONS_DIR / "020_add_email_templates.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (20, "Add email_templates table"),
        )
        conn.commit()

    if 22 not in applied:
        sql = (_MIGRATIONS_DIR / "022_add_opportunity_fields.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (22, "Add is_opportunity, opportunity_status, target_effective_date to policies"),
        )
        conn.commit()

    if 21 not in applied:
        sql = (_MIGRATIONS_DIR / "021_add_first_named_insured.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (21, "Add first_named_insured column to policies"),
        )
        conn.commit()

    if 23 not in applied:
        sql = (_MIGRATIONS_DIR / "023_add_client_fields.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (23, "Add website, renewal_month, client_since, preferred_contact_method, referral_source to clients"),
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
