"""Import session lifecycle and source profile management.

Tracks each import/reconcile run as a session with metadata (source, file,
as-of date, outcome stats).  Source profiles remember column mappings so
returning sources auto-map.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
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
