"""Import session lifecycle, source profile management, and field provenance.

Tracks each import/reconcile run as a session with metadata (source, file,
as-of date, outcome stats).  Source profiles remember column mappings so
returning sources auto-map.  Field provenance records which source set which
value and when, with conflict detection via trust × recency scoring.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, date as _date
from sqlite3 import Connection

logger = logging.getLogger(__name__)


# ── Import Sessions ───────────────────────────────────────────────────────────

def create_session(
    conn: Connection,
    source_name: str,
    source_type: str = "csv",
    file_name: str = "",
    file_content: bytes | None = None,
    as_of_date: str = "",
    client_id: int | None = None,
    column_mapping: dict | None = None,
    notes: str = "",
) -> int:
    """Create a new import session.  Returns the session id."""
    file_hash = hashlib.sha256(file_content).hexdigest() if file_content else None
    col_map_json = json.dumps(column_mapping) if column_mapping else None

    cur = conn.execute(
        """INSERT INTO import_sessions
           (source_name, source_type, file_name, file_hash, as_of_date, client_id,
            column_mapping, notes, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'in_progress')""",
        (source_name, source_type, file_name, file_hash,
         as_of_date or None, client_id, col_map_json, notes),
    )
    conn.commit()
    session_id = cur.lastrowid
    logger.info("Import session %d created: source='%s' file='%s'", session_id, source_name, file_name)
    return session_id


def complete_session(
    conn: Connection,
    session_id: int,
    row_count: int = 0,
    matched_count: int = 0,
    created_count: int = 0,
    updated_count: int = 0,
    skipped_count: int = 0,
) -> None:
    """Mark an import session as completed with outcome stats."""
    conn.execute(
        """UPDATE import_sessions
           SET status = 'completed', row_count = ?, matched_count = ?,
               created_count = ?, updated_count = ?, skipped_count = ?
           WHERE id = ?""",
        (row_count, matched_count, created_count, updated_count, skipped_count, session_id),
    )
    conn.commit()
    logger.info(
        "Import session %d completed: %d rows, %d matched, %d created, %d updated",
        session_id, row_count, matched_count, created_count, updated_count,
    )


def cancel_session(conn: Connection, session_id: int) -> None:
    """Mark an import session as cancelled."""
    conn.execute("UPDATE import_sessions SET status = 'cancelled' WHERE id = ?", (session_id,))
    conn.commit()


def get_session(conn: Connection, session_id: int) -> dict | None:
    """Get a session by id."""
    row = conn.execute("SELECT * FROM import_sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def get_recent_sessions(conn: Connection, limit: int = 20, client_id: int | None = None) -> list[dict]:
    """Get recent import sessions, optionally filtered by client."""
    if client_id:
        rows = conn.execute(
            "SELECT * FROM import_sessions WHERE client_id = ? ORDER BY imported_at DESC LIMIT ?",
            (client_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM import_sessions ORDER BY imported_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def check_duplicate_file(conn: Connection, file_content: bytes, source_name: str = "") -> dict | None:
    """Check if the same file was already uploaded.  Returns the prior session or None."""
    file_hash = hashlib.sha256(file_content).hexdigest()
    conditions = ["file_hash = ?"]
    params: list = [file_hash]
    if source_name:
        conditions.append("source_name = ?")
        params.append(source_name)
    row = conn.execute(
        f"SELECT * FROM import_sessions WHERE {' AND '.join(conditions)} ORDER BY imported_at DESC LIMIT 1",
        params,
    ).fetchone()
    return dict(row) if row else None


# ── Source Profiles ───────────────────────────────────────────────────────────

def get_source_profile(conn: Connection, source_name: str) -> dict | None:
    """Get a source profile by name."""
    row = conn.execute(
        "SELECT * FROM import_source_profiles WHERE source_name = ?",
        (source_name,),
    ).fetchone()
    return dict(row) if row else None


def get_all_source_profiles(conn: Connection) -> list[dict]:
    """Get all source profiles ordered by last use."""
    rows = conn.execute(
        "SELECT * FROM import_source_profiles ORDER BY last_used DESC NULLS LAST"
    ).fetchall()
    return [dict(r) for r in rows]


def save_source_profile(
    conn: Connection,
    source_name: str,
    column_map: dict | None = None,
    field_trust: dict | None = None,
    source_type: str = "csv",
    display_name: str = "",
    notes: str = "",
) -> int:
    """Create or update a source profile.  Returns the profile id."""
    existing = get_source_profile(conn, source_name)
    if existing:
        # Merge: update non-None fields, preserve existing for None
        updates = ["last_used = CURRENT_TIMESTAMP", "use_count = use_count + 1"]
        params: list = []
        if column_map is not None:
            updates.append("column_map = ?")
            params.append(json.dumps(column_map))
        if field_trust is not None:
            updates.append("field_trust = ?")
            params.append(json.dumps(field_trust))
        if display_name:
            updates.append("display_name = ?")
            params.append(display_name)
        if source_type:
            updates.append("source_type = ?")
            params.append(source_type)
        if notes:
            updates.append("notes = ?")
            params.append(notes)
        params.append(existing["id"])
        conn.execute(
            f"UPDATE import_source_profiles SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
        return existing["id"]
    else:
        cur = conn.execute(
            """INSERT INTO import_source_profiles
               (source_name, display_name, source_type, column_map, field_trust, last_used, use_count, notes)
               VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1, ?)""",
            (source_name, display_name or source_name, source_type,
             json.dumps(column_map or {}), json.dumps(field_trust or {}), notes),
        )
        conn.commit()
        return cur.lastrowid


def get_saved_column_map(conn: Connection, source_name: str) -> dict:
    """Get the saved column mapping for a source.  Returns empty dict if none."""
    profile = get_source_profile(conn, source_name)
    if not profile:
        return {}
    try:
        return json.loads(profile.get("column_map") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


def get_field_trust(conn: Connection, source_name: str) -> dict:
    """Get the field trust scores for a source.  Returns empty dict if none."""
    profile = get_source_profile(conn, source_name)
    if not profile:
        return {}
    try:
        return json.loads(profile.get("field_trust") or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}


# ── Field Provenance ──────────────────────────────────────────────────────────

def record_provenance(
    conn: Connection,
    policy_id: int,
    field_name: str,
    value: str,
    source_name: str = "",
    source_session_id: int | None = None,
    as_of_date: str = "",
    prior_value: str = "",
    was_conflict: bool = False,
) -> int:
    """Record a field provenance entry.  Returns the provenance row id."""
    cur = conn.execute(
        """INSERT INTO import_field_provenance
           (policy_id, field_name, value, source_name, source_session_id,
            as_of_date, prior_value, was_conflict)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (policy_id, field_name, str(value) if value is not None else "",
         source_name, source_session_id,
         as_of_date or None, str(prior_value) if prior_value else "",
         1 if was_conflict else 0),
    )
    return cur.lastrowid


def record_provenance_batch(
    conn: Connection,
    policy_id: int,
    fields: dict[str, str],
    source_name: str = "",
    source_session_id: int | None = None,
    as_of_date: str = "",
    prior_values: dict[str, str] | None = None,
) -> int:
    """Record provenance for multiple fields at once.  Returns count recorded."""
    prior_values = prior_values or {}
    count = 0
    for field_name, value in fields.items():
        if value is None:
            continue
        prior = prior_values.get(field_name, "")
        was_conflict = bool(prior and str(prior).strip() and str(prior).strip() != str(value).strip())
        record_provenance(
            conn, policy_id, field_name, value,
            source_name=source_name, source_session_id=source_session_id,
            as_of_date=as_of_date, prior_value=prior, was_conflict=was_conflict,
        )
        count += 1
    return count


def get_provenance_for_policy(conn: Connection, policy_id: int) -> list[dict]:
    """Get all provenance entries for a policy, newest first."""
    rows = conn.execute(
        """SELECT p.*, s.file_name AS session_file
           FROM import_field_provenance p
           LEFT JOIN import_sessions s ON p.source_session_id = s.id
           WHERE p.policy_id = ?
           ORDER BY p.applied_at DESC""",
        (policy_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_provenance_for_field(conn: Connection, policy_id: int, field_name: str) -> list[dict]:
    """Get provenance history for a specific field on a policy."""
    rows = conn.execute(
        """SELECT p.*, s.file_name AS session_file
           FROM import_field_provenance p
           LEFT JOIN import_sessions s ON p.source_session_id = s.id
           WHERE p.policy_id = ? AND p.field_name = ?
           ORDER BY p.applied_at DESC""",
        (policy_id, field_name),
    ).fetchall()
    return [dict(r) for r in rows]


def get_conflict_fields(conn: Connection, policy_id: int) -> list[str]:
    """Get field names that have had conflicts (value overwritten from different source)."""
    rows = conn.execute(
        "SELECT DISTINCT field_name FROM import_field_provenance "
        "WHERE policy_id = ? AND was_conflict = 1",
        (policy_id,),
    ).fetchall()
    return [r["field_name"] for r in rows]


def get_provenance_stats(conn: Connection, policy_id: int) -> dict:
    """Get provenance statistics for a policy."""
    row = conn.execute(
        """SELECT COUNT(*) as total,
                  COUNT(DISTINCT field_name) as fields_tracked,
                  COUNT(DISTINCT source_name) as sources,
                  SUM(CASE WHEN was_conflict = 1 THEN 1 ELSE 0 END) as conflicts
           FROM import_field_provenance WHERE policy_id = ?""",
        (policy_id,),
    ).fetchone()
    return dict(row) if row else {"total": 0, "fields_tracked": 0, "sources": 0, "conflicts": 0}


# ── Conflict Resolution ──────────────────────────────────────────────────────

def compute_effective_priority(
    trust_weight: float,
    as_of_date: str | None,
    today: str | None = None,
) -> float:
    """Compute effective priority = trust_weight × recency_factor.

    Recency factor decays from 1.0 (today) to 0.5 (1 year old) linearly.
    Data with no as_of_date gets recency=0.7 (moderate penalty).
    """
    if not today:
        today = _date.today().isoformat()
    if not as_of_date:
        return trust_weight * 0.7

    try:
        as_of = datetime.fromisoformat(as_of_date).date()
        today_d = datetime.fromisoformat(today).date()
        days_old = (today_d - as_of).days
        if days_old < 0:
            days_old = 0
        # Linear decay: 1.0 at 0 days → 0.5 at 365 days, floor at 0.3
        recency = max(0.3, 1.0 - (days_old / 730.0))
        return trust_weight * recency
    except (ValueError, TypeError):
        return trust_weight * 0.7


def check_conflict(
    conn: Connection,
    policy_id: int,
    field_name: str,
    new_value: str,
    new_source_name: str,
    new_as_of_date: str = "",
) -> dict | None:
    """Check if setting this field would conflict with existing provenance.

    Returns None if no conflict (safe to apply), or a dict with:
    {
        "existing_value": str, "existing_source": str, "existing_as_of": str,
        "existing_priority": float, "new_priority": float,
        "recommendation": "apply" | "keep_existing" | "ask_user"
    }
    """
    import policydb.config as _cfg

    # Get the most recent provenance for this field from a DIFFERENT source
    row = conn.execute(
        """SELECT value, source_name, as_of_date
           FROM import_field_provenance
           WHERE policy_id = ? AND field_name = ? AND source_name != ?
           ORDER BY applied_at DESC LIMIT 1""",
        (policy_id, field_name, new_source_name),
    ).fetchone()

    if not row:
        return None  # No prior from a different source — no conflict

    existing_value = row["value"] or ""
    existing_source = row["source_name"] or ""
    existing_as_of = row["as_of_date"] or ""

    # If values are the same, no real conflict
    if existing_value.strip() == str(new_value).strip():
        return None

    # Get trust weights from config
    trust_defaults = _cfg.get("field_trust_defaults", {})
    new_trust = trust_defaults.get(new_source_name, {}).get(field_name, 50)
    existing_trust = trust_defaults.get(existing_source, {}).get(field_name, 50)

    # Also check profile-level trust overrides
    new_profile = get_source_profile(conn, new_source_name)
    if new_profile:
        try:
            profile_trust = json.loads(new_profile.get("field_trust") or "{}")
            if field_name in profile_trust:
                new_trust = profile_trust[field_name]
        except Exception:
            pass
    existing_profile = get_source_profile(conn, existing_source)
    if existing_profile:
        try:
            profile_trust = json.loads(existing_profile.get("field_trust") or "{}")
            if field_name in profile_trust:
                existing_trust = profile_trust[field_name]
        except Exception:
            pass

    new_priority = compute_effective_priority(new_trust, new_as_of_date)
    existing_priority = compute_effective_priority(existing_trust, existing_as_of)

    # Determine recommendation
    diff = new_priority - existing_priority
    if diff > 10:
        recommendation = "apply"
    elif diff < -10:
        recommendation = "keep_existing"
    else:
        recommendation = "ask_user"

    return {
        "existing_value": existing_value,
        "existing_source": existing_source,
        "existing_as_of": existing_as_of,
        "existing_priority": round(existing_priority, 1),
        "new_priority": round(new_priority, 1),
        "recommendation": recommendation,
    }
