"""Database connection, schema initialization, and migrations."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path


DB_DIR = Path.home() / ".policydb"
DB_PATH = DB_DIR / "policydb.sqlite"
EXPORTS_DIR = DB_DIR / "exports"
CONFIG_PATH = DB_DIR / "config.yaml"

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_HEALTH_STATUS: dict = {
    "integrity": "ok",
    "fk_violations": 0,
    "last_backup": None,
    "last_backup_verified": False,
    "backup_count": 0,
    "db_size": 0,
    "wal_size": 0,
}


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


def _backup_db(db_path: Path) -> None:
    """Copy the database file to a timestamped backup before any migrations run."""
    import shutil
    import datetime
    if not db_path.exists():
        return
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.parent / f"policydb.sqlite.backup_{ts}"
    shutil.copy2(db_path, backup_path)


def _auto_backup(db_path: Path, max_backups: int = 30, force: bool = False) -> None:
    """Create a verified backup in ~/.policydb/backups/, pruning old ones."""
    import shutil
    import datetime

    if not db_path.exists():
        return

    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)

    # Find existing backups sorted newest-first
    existing = sorted(
        backup_dir.glob("policydb_*.sqlite"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    # Skip if a backup exists within the last hour (unless forced)
    if not force and existing:
        newest_mtime = existing[0].stat().st_mtime
        age_seconds = datetime.datetime.now().timestamp() - newest_mtime
        if age_seconds < 3600:
            _HEALTH_STATUS["backup_count"] = len(existing)
            _HEALTH_STATUS["last_backup"] = str(existing[0])
            return

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = backup_dir / f"policydb_{ts}.sqlite"
    shutil.copy2(db_path, backup_path)

    # Verify backup integrity
    verified = False
    try:
        bconn = sqlite3.connect(str(backup_path))
        result = bconn.execute("PRAGMA integrity_check").fetchone()
        verified = result is not None and result[0] == "ok"
        bconn.close()
    except Exception:
        verified = False

    _HEALTH_STATUS["last_backup"] = str(backup_path)
    _HEALTH_STATUS["last_backup_verified"] = verified

    # Refresh the list after adding the new backup
    existing = sorted(
        backup_dir.glob("policydb_*.sqlite"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    _HEALTH_STATUS["backup_count"] = len(existing)

    # Prune backups beyond max_backups
    for old_backup in existing[max_backups:]:
        try:
            old_backup.unlink()
        except Exception:
            pass


def _run_hygiene_062(conn: sqlite3.Connection) -> None:
    """One-time normalization of existing data: normalize policy types, policy numbers,
    client names, and address fields to canonical forms."""
    from policydb.utils import (normalize_coverage_type, normalize_policy_number,
                                 normalize_client_name, format_zip, format_state, format_city)
    changed = {"policy_type": 0, "policy_number": 0, "client_name": 0, "zip": 0, "state": 0, "city": 0}

    for r in conn.execute("SELECT id, policy_type FROM policies WHERE policy_type IS NOT NULL").fetchall():
        n = normalize_coverage_type(r["policy_type"])
        if n != r["policy_type"]:
            conn.execute("UPDATE policies SET policy_type = ? WHERE id = ?", (n, r["id"]))
            changed["policy_type"] += 1

    for r in conn.execute("SELECT id, policy_number FROM policies WHERE policy_number IS NOT NULL AND policy_number != ''").fetchall():
        n = normalize_policy_number(r["policy_number"])
        if n != r["policy_number"]:
            conn.execute("UPDATE policies SET policy_number = ? WHERE id = ?", (n, r["id"]))
            changed["policy_number"] += 1

    for r in conn.execute("SELECT id, name FROM clients WHERE name IS NOT NULL").fetchall():
        n = normalize_client_name(r["name"])
        if n != r["name"]:
            conn.execute("UPDATE clients SET name = ? WHERE id = ?", (n, r["id"]))
            changed["client_name"] += 1

    for r in conn.execute(
        "SELECT id, exposure_zip, exposure_state, exposure_city FROM policies WHERE exposure_zip IS NOT NULL OR exposure_state IS NOT NULL OR exposure_city IS NOT NULL"
    ).fetchall():
        updates = {}
        if r["exposure_zip"]:
            fmt = format_zip(r["exposure_zip"])
            if fmt != r["exposure_zip"]:
                updates["exposure_zip"] = fmt
                changed["zip"] += 1
        if r["exposure_state"]:
            fmt = format_state(r["exposure_state"])
            if fmt != r["exposure_state"]:
                updates["exposure_state"] = fmt
                changed["state"] += 1
        if r["exposure_city"]:
            fmt = format_city(r["exposure_city"])
            if fmt != r["exposure_city"]:
                updates["exposure_city"] = fmt
                changed["city"] += 1
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            conn.execute(f"UPDATE policies SET {set_clause} WHERE id = ?", (*updates.values(), r["id"]))  # noqa: S608

    conn.commit()
    total = sum(changed.values())
    if total > 0:
        print(f"[hygiene-062] Normalized {total} fields: {changed}")


def init_db(path: Path | None = None) -> None:
    """Create schema, run pending migrations, create views."""
    ensure_dirs()
    db_path = path or DB_PATH
    conn = get_connection(path)

    # Recover from a partial migration-024: policies was dropped and renamed to
    # policies_new but the RENAME never completed. Do this before reading
    # applied versions so the rest of init_db can proceed normally.
    _tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "policies" not in _tables and "policies_new" in _tables:
        conn.executescript("""
            DROP VIEW IF EXISTS v_policy_status;
            DROP VIEW IF EXISTS v_client_summary;
            DROP VIEW IF EXISTS v_schedule;
            DROP VIEW IF EXISTS v_tower;
            DROP VIEW IF EXISTS v_renewal_pipeline;
            DROP VIEW IF EXISTS v_overdue_followups;
            ALTER TABLE policies_new RENAME TO policies;
            CREATE TRIGGER IF NOT EXISTS policies_updated_at
            AFTER UPDATE ON policies
            BEGIN
                UPDATE policies SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
            END;
        """)
        # Record migration 24 as applied if it isn't yet
        already = {r[0] for r in conn.execute("SELECT version FROM schema_version").fetchall()}
        if 24 not in already:
            conn.execute(
                "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                (24, "Remove NOT NULL from policies.effective_date, expiration_date, carrier for opportunity support"),
            )
            conn.commit()

    applied = _get_applied_versions(conn)

    # Back up the database once before running any pending migrations.
    # This gives a clean restore point regardless of which migration fails.
    _KNOWN_MIGRATIONS = {1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63}
    if _KNOWN_MIGRATIONS - applied:
        _backup_db(db_path)

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

    if 24 not in applied:
        # Clean up any leftover from a previous failed attempt
        conn.executescript("DROP TABLE IF EXISTS policies_new;")
        sql = (_MIGRATIONS_DIR / "024_nullable_policy_dates.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (24, "Remove NOT NULL from policies.effective_date, expiration_date, carrier for opportunity support"),
        )
        conn.commit()

    if 25 not in applied:
        sql = (_MIGRATIONS_DIR / "025_add_policy_contact_organization.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (25, "Add organization column to policy_contacts"),
        )
        conn.commit()

    if 26 not in applied:
        sql = (_MIGRATIONS_DIR / "026_add_projects_table.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (26, "Add projects table as canonical project/location registry"),
        )
        conn.commit()

    if 27 not in applied:
        sql = (_MIGRATIONS_DIR / "027_add_access_point.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (27, "Add access_point field to policies"),
        )
        conn.commit()

    if 28 not in applied:
        sql = (_MIGRATIONS_DIR / "028_add_internal_assignment.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (28, "Add assignment field to client_contacts for internal team members"),
        )
        conn.commit()

    if 29 not in applied:
        sql = (_MIGRATIONS_DIR / "029_add_reviewed_at.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (29, "Add last_reviewed_at and review_cycle to policies and clients"),
        )
        conn.commit()

    if 30 not in applied:
        sql = (_MIGRATIONS_DIR / "030_add_activity_duration.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (30, "Add duration_minutes to activity_log"),
        )
        conn.commit()

    if 31 not in applied:
        sql = (_MIGRATIONS_DIR / "031_add_duration_hours.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (31, "Add duration_hours to activity_log"),
        )
        conn.commit()

    if 32 not in applied:
        sql = (_MIGRATIONS_DIR / "032_add_milestone_critical.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (32, "Add is_critical to policy_milestones"),
        )
        conn.commit()

    if 33 not in applied:
        sql = (_MIGRATIONS_DIR / "033_add_is_placement_colleague.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (33, "Add is_placement_colleague flag to policy_contacts"),
        )
        conn.commit()

    if 34 not in applied:
        sql = (_MIGRATIONS_DIR / "034_add_policy_contact_notes.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (34, "Add notes column to policy_contacts"),
        )
        conn.commit()

    if 35 not in applied:
        sql = (_MIGRATIONS_DIR / "035_add_client_risks.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (35, "Add client_risks table for exposure tracking"),
        )
        conn.commit()

    if 36 not in applied:
        sql = (_MIGRATIONS_DIR / "036_add_mobile_phone.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (36, "Add mobile phone column to contact tables"),
        )
        conn.commit()

    if 37 not in applied:
        sql = (_MIGRATIONS_DIR / "037_add_is_prospect.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (37, "Add is_prospect flag to clients"),
        )
        conn.commit()

    if 38 not in applied:
        sql = (_MIGRATIONS_DIR / "038_risk_redesign.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (38, "Risk redesign: add source/review_date/identified_date, coverage lines, and controls tables"),
        )
        conn.commit()

    if 39 not in applied:
        sql = (_MIGRATIONS_DIR / "039_add_linked_accounts.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (39, "Add client_groups and client_group_members tables for linked accounts"),
        )
        conn.commit()

    if 40 not in applied:
        sql = (_MIGRATIONS_DIR / "040_add_policy_scratchpad.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (40, "Add policy_scratchpad table for per-policy working notes"),
        )
        conn.commit()

    if 41 not in applied:
        sql = (_MIGRATIONS_DIR / "041_add_saved_notes.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (41, "Add saved_notes table for pinned scratchpad entries"),
        )
        conn.commit()

    if 42 not in applied:
        sql = (_MIGRATIONS_DIR / "042_add_client_contact_org.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (42, "Add organization column to client_contacts"),
        )
        conn.commit()

    if 43 not in applied:
        sql = (_MIGRATIONS_DIR / "043_add_billing_accounts.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (43, "Add billing_accounts table for master and alternate billing IDs"),
        )
        conn.commit()

    if 44 not in applied:
        sql = (_MIGRATIONS_DIR / "044_add_fein.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (44, "Add FEIN column to clients and billing_accounts"),
        )
        conn.commit()

    if 45 not in applied:
        sql = (_MIGRATIONS_DIR / "045_add_billing_entity_name.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (45, "Add entity_name column to billing_accounts"),
        )
        conn.commit()

    if 46 not in applied:
        sql = (_MIGRATIONS_DIR / "046_add_client_requests.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (46, "Add client_request_bundles and client_request_items"),
        )
        conn.commit()

    if 47 not in applied:
        sql = (_MIGRATIONS_DIR / "047_add_bundle_send_by_date.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (47, "Add send_by_date to client_request_bundles"),
        )
        conn.commit()

    if 48 not in applied:
        sql = (_MIGRATIONS_DIR / "048_add_rfi_uid.sql").read_text()
        conn.executescript(sql)
        _backfill_rfi_uids(conn)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (48, "Add rfi_uid to client_request_bundles"),
        )
        conn.commit()

    if 49 not in applied:
        sql = (_MIGRATIONS_DIR / "049_add_mandated_activity_tracking.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (49, "Add mandated_activity_log table"),
        )
        conn.commit()

    if 50 not in applied:
        sql = (_MIGRATIONS_DIR / "050_unified_contacts.sql").read_text()
        conn.executescript(sql)
        _migrate_unified_contacts(conn)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (50, "Unified contacts schema with junction tables"),
        )
        conn.commit()

    if 51 not in applied:
        sql = (_MIGRATIONS_DIR / "051_add_hourly_rate.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (51, "Add hourly_rate to clients"),
        )
        conn.commit()

    if 52 not in applied:
        sql = (_MIGRATIONS_DIR / "052_add_program_fields.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (52, "Add program fields to policies"),
        )
        conn.commit()

    if 53 not in applied:
        sql = (_MIGRATIONS_DIR / "053_add_program_id.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (53, "Add program_id FK to policies for program linkage"),
        )
        conn.commit()

    if 54 not in applied:
        sql = (_MIGRATIONS_DIR / "054_add_client_followup.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (54, "Add follow_up_date to clients"),
        )
        conn.commit()

    if 55 not in applied:
        sql = (_MIGRATIONS_DIR / "055_add_meetings.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (55, "Add meetings tables"),
        )
        conn.commit()

    if 56 not in applied:
        sql = (_MIGRATIONS_DIR / "056_meeting_enhancements.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (56, "Meeting policy links and action item policy_uid"),
        )
        conn.commit()

    if 57 not in applied:
        sql = (_MIGRATIONS_DIR / "057_meeting_uid_and_attendee_type.sql").read_text()
        conn.executescript(sql)
        _backfill_meeting_uids(conn)
        _backfill_attendee_type(conn)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (57, "Meeting UIDs, attendee type freeform, fix CNCN RFI UIDs"),
        )
        conn.commit()

    if 58 not in applied:
        sql = (_MIGRATIONS_DIR / "058_program_carriers_table.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (58, "Add program_carriers table for structured program carrier rows"),
        )
        conn.commit()

    if 59 not in applied:
        sql = (_MIGRATIONS_DIR / "059_followup_threading.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (59, "Add disposition and thread_id columns to activity_log for follow-up threading"),
        )
        conn.commit()

    if 60 not in applied:
        sql = (_MIGRATIONS_DIR / "060_add_is_bor.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (60, "Add is_bor flag to policies for Broker of Record tracking"),
        )
        conn.commit()

    if 61 not in applied:
        sql = (_MIGRATIONS_DIR / "061_project_pipeline.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (61, "Add project pipeline columns: type, status, value, dates, location, contacts"),
        )
        conn.commit()

    if 62 not in applied:
        sql = (_MIGRATIONS_DIR / "062_normalize_existing_data.sql").read_text()
        conn.executescript(sql)
        _run_hygiene_062(conn)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (62, "One-time data hygiene: normalize policy types, policy numbers, client names, and address fields"),
        )
        conn.commit()

    if 63 not in applied:
        sql = (_MIGRATIONS_DIR / "063_contact_expertise.sql").read_text()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_version (version, description) VALUES (?, ?)",
            (63, "Add contact_expertise table and expertise_notes column to contacts"),
        )
        conn.commit()

    # Data hygiene: fix 'None' string corruption in text fields (runs every startup, fast no-op if clean)
    conn.execute("UPDATE clients SET cn_number = NULL WHERE cn_number = 'None'")

    # Backfill project addresses from linked policies (idempotent — only fills empty project addresses)
    conn.execute("""
        UPDATE projects SET
            address = (SELECT p.exposure_address FROM policies p WHERE p.project_id = projects.id AND p.exposure_address IS NOT NULL AND p.exposure_address != '' LIMIT 1),
            city = (SELECT p.exposure_city FROM policies p WHERE p.project_id = projects.id AND p.exposure_city IS NOT NULL AND p.exposure_city != '' LIMIT 1),
            state = (SELECT p.exposure_state FROM policies p WHERE p.project_id = projects.id AND p.exposure_state IS NOT NULL AND p.exposure_state != '' LIMIT 1),
            zip = (SELECT p.exposure_zip FROM policies p WHERE p.project_id = projects.id AND p.exposure_zip IS NOT NULL AND p.exposure_zip != '' LIMIT 1)
        WHERE (project_type = 'Location' OR project_type IS NULL)
          AND (address IS NULL OR address = '')
          AND EXISTS (SELECT 1 FROM policies p WHERE p.project_id = projects.id AND p.exposure_address IS NOT NULL AND p.exposure_address != '')
    """)

    _create_views(conn)
    conn.commit()

    # Generate mandated activities (runs every startup, idempotent via tracking table)
    from policydb.queries import generate_mandated_activities
    generate_mandated_activities(conn)

    # Health checks — wrapped in try/except so they don't block server start
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as e:
        print(f"[WARNING] WAL checkpoint failed (DB may be locked by another process): {e}")

    try:
        _integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        _HEALTH_STATUS["integrity"] = _integrity
        if _integrity != "ok":
            print(f"[WARNING] DB integrity: {_integrity}")
    except Exception as e:
        _HEALTH_STATUS["integrity"] = f"error: {e}"
        print(f"[WARNING] Integrity check failed: {e}")

    try:
        _fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        # Auto-fix: clean up legacy table FK violations (harmless orphaned records)
        if _fk_violations:
            _legacy_tables = {"client_contacts_legacy", "policy_contacts_legacy"}
            _legacy_violations = [v for v in _fk_violations if v["table"] in _legacy_tables]
            _real_violations = [v for v in _fk_violations if v["table"] not in _legacy_tables]
            if _legacy_violations:
                for lt in _legacy_tables:
                    try:
                        conn.execute(f"DELETE FROM {lt} WHERE rowid IN (SELECT rowid FROM {lt} t WHERE NOT EXISTS (SELECT 1 FROM policies p WHERE p.id = t.policy_id))")
                    except Exception:
                        pass
                conn.commit()
                print(f"[hygiene] Cleaned {len(_legacy_violations)} orphaned legacy contact records")
                # Re-check after cleanup
                _fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        _HEALTH_STATUS["fk_violations"] = len(_fk_violations)
        _HEALTH_STATUS["fk_details"] = [{"table": v["table"], "rowid": v["rowid"], "parent": v["parent"]} for v in _fk_violations[:20]]
        if _fk_violations:
            print(f"[WARNING] {len(_fk_violations)} FK violation(s) detected")
    except Exception as e:
        print(f"[WARNING] FK check failed: {e}")

    # DB size
    _HEALTH_STATUS["db_size"] = os.path.getsize(db_path) if os.path.exists(db_path) else 0
    _wal = str(db_path) + "-wal"
    _HEALTH_STATUS["wal_size"] = os.path.getsize(_wal) if os.path.exists(_wal) else 0

    conn.close()

    # Auto-backup (runs after connection is closed so the file is fully flushed)
    from policydb import config as _cfg
    _auto_backup(db_path, max_backups=_cfg.get("backup_retention_count", 30))


def _migrate_unified_contacts(conn: sqlite3.Connection) -> None:
    """One-time data migration: populate contacts + junction tables from legacy tables."""
    from rapidfuzz import fuzz

    # Check if old tables exist (they might have been renamed already on a retry)
    _tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "client_contacts" not in _tables and "policy_contacts" not in _tables:
        return  # Already migrated

    # 1. Build unified contacts from all sources
    # Gather all distinct names with best non-null values for shared fields
    name_data: dict[str, dict] = {}  # keyed by LOWER(TRIM(name))

    def _merge(key: str, name: str, email=None, phone=None, mobile=None, org=None):
        if key not in name_data:
            name_data[key] = {"name": name, "email": None, "phone": None, "mobile": None, "organization": None}
        d = name_data[key]
        if email and not d["email"]:
            d["email"] = email
        if phone and not d["phone"]:
            d["phone"] = phone
        if mobile and not d["mobile"]:
            d["mobile"] = mobile
        if org and not d["organization"]:
            d["organization"] = org

    # From client_contacts
    if "client_contacts" in _tables:
        for r in conn.execute(
            "SELECT name, email, phone, mobile, organization FROM client_contacts WHERE name IS NOT NULL AND TRIM(name) != ''"
        ).fetchall():
            key = r["name"].strip().lower()
            _merge(key, r["name"].strip(), r["email"], r["phone"], r["mobile"],
                   r["organization"] if "organization" in r.keys() else None)

    # From policy_contacts
    if "policy_contacts" in _tables:
        for r in conn.execute(
            "SELECT name, email, phone, mobile, organization FROM policy_contacts WHERE name IS NOT NULL AND TRIM(name) != ''"
        ).fetchall():
            key = r["name"].strip().lower()
            _merge(key, r["name"].strip(), r["email"], r["phone"], r["mobile"], r["organization"])

    # From legacy client fields
    for r in conn.execute(
        "SELECT id, primary_contact, contact_email, contact_phone, contact_mobile FROM clients WHERE primary_contact IS NOT NULL AND TRIM(primary_contact) != ''"
    ).fetchall():
        key = r["primary_contact"].strip().lower()
        _merge(key, r["primary_contact"].strip(), r["contact_email"], r["contact_phone"], r["contact_mobile"])

    # Insert into contacts table
    contact_id_map: dict[str, int] = {}  # key -> contacts.id
    for key, d in name_data.items():
        try:
            cur = conn.execute(
                "INSERT INTO contacts (name, email, phone, mobile, organization) VALUES (?,?,?,?,?)",
                (d["name"], d["email"], d["phone"], d["mobile"], d["organization"]),
            )
            contact_id_map[key] = cur.lastrowid
        except Exception:
            # Unique index conflict — fetch existing
            existing = conn.execute(
                "SELECT id FROM contacts WHERE LOWER(TRIM(name)) = ?", (key,)
            ).fetchone()
            if existing:
                contact_id_map[key] = existing["id"]

    # 2. Rebuild client assignments from client_contacts
    if "client_contacts" in _tables:
        for r in conn.execute(
            "SELECT * FROM client_contacts WHERE name IS NOT NULL AND TRIM(name) != ''"
        ).fetchall():
            key = r["name"].strip().lower()
            cid = contact_id_map.get(key)
            if not cid or not r["client_id"]:
                continue
            ct = r["contact_type"] if "contact_type" in r.keys() else "client"
            try:
                conn.execute(
                    """INSERT INTO contact_client_assignments
                       (contact_id, client_id, contact_type, role, title, assignment, notes, is_primary)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (cid, r["client_id"], ct,
                     r["role"] if "role" in r.keys() else None,
                     r["title"] if "title" in r.keys() else None,
                     r["assignment"] if "assignment" in r.keys() else None,
                     r["notes"] if "notes" in r.keys() else None,
                     r["is_primary"] if "is_primary" in r.keys() else 0),
                )
            except Exception:
                pass  # UNIQUE constraint — skip duplicate

    # 3. Rebuild policy assignments from policy_contacts
    if "policy_contacts" in _tables:
        for r in conn.execute(
            "SELECT * FROM policy_contacts WHERE name IS NOT NULL AND TRIM(name) != ''"
        ).fetchall():
            key = r["name"].strip().lower()
            cid = contact_id_map.get(key)
            if not cid or not r["policy_id"]:
                continue
            try:
                conn.execute(
                    """INSERT INTO contact_policy_assignments
                       (contact_id, policy_id, role, title, notes, is_placement_colleague)
                       VALUES (?,?,?,?,?,?)""",
                    (cid, r["policy_id"],
                     r["role"] if "role" in r.keys() else None,
                     r["title"] if "title" in r.keys() else None,
                     r["notes"] if "notes" in r.keys() else None,
                     r["is_placement_colleague"] if "is_placement_colleague" in r.keys() else 0),
                )
            except Exception:
                pass  # UNIQUE constraint — skip duplicate

    # 4. Link activity_log: fuzzy-match contact_person to contacts
    activities = conn.execute(
        "SELECT id, contact_person FROM activity_log WHERE contact_person IS NOT NULL AND TRIM(contact_person) != '' AND contact_id IS NULL"
    ).fetchall()
    contact_names = list(name_data.keys())
    contact_display_names = {k: v["name"] for k, v in name_data.items()}
    for a in activities:
        cp = a["contact_person"].strip().lower()
        # Exact match first
        if cp in contact_id_map:
            conn.execute("UPDATE activity_log SET contact_id=? WHERE id=?", (contact_id_map[cp], a["id"]))
            continue
        # Fuzzy match
        if contact_names:
            best_score = 0
            best_key = None
            for cn in contact_names:
                score = fuzz.ratio(cp, cn)
                if score > best_score:
                    best_score = score
                    best_key = cn
            if best_score >= 85 and best_key:
                conn.execute("UPDATE activity_log SET contact_id=? WHERE id=?", (contact_id_map[best_key], a["id"]))

    # 5. Migrate legacy client primary_contact fields
    for r in conn.execute(
        "SELECT id, primary_contact, contact_email, contact_phone, contact_mobile FROM clients WHERE primary_contact IS NOT NULL AND TRIM(primary_contact) != ''"
    ).fetchall():
        key = r["primary_contact"].strip().lower()
        cid = contact_id_map.get(key)
        if not cid:
            continue
        # Ensure a contact_client_assignment with is_primary=1 exists
        existing = conn.execute(
            "SELECT id FROM contact_client_assignments WHERE contact_id=? AND client_id=? AND contact_type='client'",
            (cid, r["id"]),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE contact_client_assignments SET is_primary=1 WHERE id=?",
                (existing["id"],),
            )
        else:
            try:
                conn.execute(
                    "INSERT INTO contact_client_assignments (contact_id, client_id, contact_type, is_primary) VALUES (?,?,?,1)",
                    (cid, r["id"], "client"),
                )
            except Exception:
                pass

    # 6. Rename old tables as backup
    if "client_contacts" in _tables:
        conn.execute("ALTER TABLE client_contacts RENAME TO client_contacts_legacy")
    if "policy_contacts" in _tables:
        conn.execute("ALTER TABLE policy_contacts RENAME TO policy_contacts_legacy")


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


def next_rfi_uid(conn: sqlite3.Connection, client_id: int) -> str:
    """Generate next RFI UID for a client: CN{number}-RFI01, CN{number}-RFI02, etc."""
    client = conn.execute(
        "SELECT cn_number FROM clients WHERE id=?", (client_id,)
    ).fetchone()
    cn = client["cn_number"] if client and client["cn_number"] else None
    # Strip leading "CN" prefix if already present to avoid duplication (e.g. "CN122333627" → "122333627")
    cn_clean = re.sub(r'^[Cc][Nn]', '', cn) if cn else ""
    prefix = f"CN{cn_clean}" if cn_clean else f"C{client_id}"

    row = conn.execute(
        "SELECT rfi_uid FROM client_request_bundles WHERE client_id=? AND rfi_uid IS NOT NULL ORDER BY id DESC LIMIT 1",
        (client_id,),
    ).fetchone()
    max_num = 0
    if row and row["rfi_uid"]:
        try:
            max_num = int(row["rfi_uid"].rsplit("-RFI", 1)[1])
        except (IndexError, ValueError):
            pass
    return f"{prefix}-RFI{max_num + 1:02d}"


def next_meeting_uid(conn: sqlite3.Connection, client_id: int) -> str:
    """Generate next meeting UID for a client: CN{number}-MTG01, CN{number}-MTG02, etc."""
    client = conn.execute(
        "SELECT cn_number FROM clients WHERE id=?", (client_id,)
    ).fetchone()
    cn = client["cn_number"] if client and client["cn_number"] else None
    cn_clean = re.sub(r'^[Cc][Nn]', '', cn) if cn else ""
    prefix = f"CN{cn_clean}" if cn_clean else f"C{client_id}"

    row = conn.execute(
        "SELECT meeting_uid FROM client_meetings WHERE client_id=? AND meeting_uid IS NOT NULL ORDER BY id DESC LIMIT 1",
        (client_id,),
    ).fetchone()
    max_num = 0
    if row and row["meeting_uid"]:
        try:
            max_num = int(row["meeting_uid"].rsplit("-MTG", 1)[1])
        except (IndexError, ValueError):
            pass
    return f"{prefix}-MTG{max_num + 1:02d}"


def _backfill_meeting_uids(conn: sqlite3.Connection) -> None:
    """Assign meeting_uid to existing meetings that lack one."""
    meetings = conn.execute(
        """SELECT m.id, m.client_id, c.cn_number
           FROM client_meetings m
           JOIN clients c ON m.client_id = c.id
           WHERE m.meeting_uid IS NULL
           ORDER BY m.client_id, m.id"""
    ).fetchall()
    client_seq: dict[int, int] = {}
    for m in meetings:
        cid = m["client_id"]
        cn = m["cn_number"]
        cn_clean = re.sub(r'^[Cc][Nn]', '', cn) if cn else ""
        prefix = f"CN{cn_clean}" if cn_clean else f"C{cid}"
        seq = client_seq.get(cid, 0) + 1
        client_seq[cid] = seq
        conn.execute(
            "UPDATE client_meetings SET meeting_uid=? WHERE id=?",
            (f"{prefix}-MTG{seq:02d}", m["id"]),
        )


def _backfill_attendee_type(conn: sqlite3.Connection) -> None:
    """Migrate is_internal flag to attendee_type text field."""
    conn.execute(
        "UPDATE meeting_attendees SET attendee_type = 'Internal' WHERE is_internal = 1 AND (attendee_type IS NULL OR attendee_type = '')"
    )
    conn.execute(
        "UPDATE meeting_attendees SET attendee_type = 'Client' WHERE is_internal = 0 AND (attendee_type IS NULL OR attendee_type = '')"
    )


def _backfill_rfi_uids(conn: sqlite3.Connection) -> None:
    """Assign rfi_uid to any existing bundles that lack one."""
    bundles = conn.execute(
        """SELECT b.id, b.client_id, c.cn_number
           FROM client_request_bundles b
           JOIN clients c ON b.client_id = c.id
           WHERE b.rfi_uid IS NULL
           ORDER BY b.client_id, b.id"""
    ).fetchall()
    client_seq: dict[int, int] = {}
    for b in bundles:
        cid = b["client_id"]
        cn = b["cn_number"]
        # Strip leading "CN" prefix if already present to avoid duplication
        cn_clean = re.sub(r'^[Cc][Nn]', '', cn) if cn else ""
        prefix = f"CN{cn_clean}" if cn_clean else f"C{cid}"
        seq = client_seq.get(cid, 0) + 1
        client_seq[cid] = seq
        conn.execute(
            "UPDATE client_request_bundles SET rfi_uid=? WHERE id=?",
            (f"{prefix}-RFI{seq:02d}", b["id"]),
        )
