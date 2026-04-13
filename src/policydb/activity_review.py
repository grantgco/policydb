"""Activity Review Engine — detect unlogged work sessions from audit trail.

Scans audit_log entries, clusters them into per-client work sessions,
identifies sessions with no corresponding activity_log entry, and writes
them to the suggested_activities table for user review.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

import policydb.config as cfg

logger = logging.getLogger(__name__)

# Tables we scan for client-attributable work
_TRACKED_TABLES = {"clients", "policies", "contacts", "policy_milestones"}

# System-generated audit entries to exclude: (table, operation) pairs that
# represent automated work, not user actions.  Milestone INSERTs are system-
# created (renewal checklist auto-populated), but UPDATEs are user actions
# (checking off a milestone).
_SYSTEM_OPS: set[tuple[str, str]] = {
    ("policy_milestones", "INSERT"),
    ("policy_milestones", "DELETE"),
}

# Summary text templates: (table, operation) -> verb
_SUMMARY_VERBS: dict[tuple[str, str], str] = {
    ("clients", "INSERT"): "Created client",
    ("clients", "UPDATE"): "Updated client info",
    ("clients", "DELETE"): "Deleted client",
    ("policies", "INSERT"): "Created policy",
    ("policies", "UPDATE"): "Updated policy",
    ("policies", "DELETE"): "Deleted policy",
    ("contacts", "INSERT"): "Added contact",
    ("contacts", "UPDATE"): "Edited contact",
    ("contacts", "DELETE"): "Removed contact",
    ("policy_milestones", "INSERT"): "Added milestone",
    ("policy_milestones", "UPDATE"): "Updated milestone",
    ("policy_milestones", "DELETE"): "Removed milestone",
}


def _resolve_client_ids(conn: sqlite3.Connection, entries: list[dict]) -> list[dict]:
    """Resolve each audit entry to one or more client_ids.

    Returns a flat list of entries, each with a 'client_id' key.
    Entries that can't be resolved are dropped.
    """
    resolved = []

    for entry in entries:
        table = entry["table_name"]
        row_id = entry["row_id"]

        if table == "clients":
            # row_id IS the client id
            try:
                entry["client_id"] = int(row_id)
                resolved.append(entry)
            except (ValueError, TypeError):
                pass

        elif table == "policies":
            # row_id is policy_uid -> policies.client_id
            row = conn.execute(
                "SELECT client_id FROM policies WHERE policy_uid = ?", (row_id,)
            ).fetchone()
            if row:
                entry["client_id"] = row[0]
                entry["_policy_uid"] = row_id
                resolved.append(entry)

        elif table == "contacts":
            # row_id -> contacts.id -> contact_client_assignments.client_id
            try:
                contact_id = int(row_id)
            except (ValueError, TypeError):
                continue
            rows = conn.execute(
                "SELECT client_id FROM contact_client_assignments WHERE contact_id = ?",
                (contact_id,),
            ).fetchall()
            for r in rows:
                copy = dict(entry)
                copy["client_id"] = r[0]
                resolved.append(copy)

        elif table == "policy_milestones":
            # row_id -> policy_milestones.id -> policy_uid -> policies.client_id
            try:
                ms_id = int(row_id)
            except (ValueError, TypeError):
                continue
            row = conn.execute(
                """SELECT p.client_id, pm.policy_uid
                   FROM policy_milestones pm
                   JOIN policies p ON p.policy_uid = pm.policy_uid
                   WHERE pm.id = ?""",
                (ms_id,),
            ).fetchone()
            if row:
                entry["client_id"] = row[0]
                entry["_policy_uid"] = row[1]
                resolved.append(entry)

    return resolved


def _cluster_sessions(
    entries: list[dict], gap_minutes: int
) -> dict[int, list[list[dict]]]:
    """Cluster entries by client_id and time gap.

    Returns {client_id: [session_list, session_list, ...]}.
    Each session_list is a list of entries within the gap window.
    """
    by_client: dict[int, list[dict]] = defaultdict(list)
    for e in entries:
        by_client[e["client_id"]].append(e)

    gap = timedelta(minutes=gap_minutes)
    result: dict[int, list[list[dict]]] = {}

    for client_id, client_entries in by_client.items():
        client_entries.sort(key=lambda e: e["changed_at"])
        sessions: list[list[dict]] = []
        current: list[dict] = []

        for e in client_entries:
            if current:
                prev_time = datetime.fromisoformat(current[-1]["changed_at"])
                this_time = datetime.fromisoformat(e["changed_at"])
                if this_time - prev_time > gap:
                    sessions.append(current)
                    current = []
            current.append(e)

        if current:
            sessions.append(current)

        result[client_id] = sessions

    return result


def _is_bulk_operation(session: list[dict]) -> bool:
    """Detect bulk operations: >20 changes within 60 seconds."""
    if len(session) <= 20:
        return False
    first = datetime.fromisoformat(session[0]["changed_at"])
    last = datetime.fromisoformat(session[-1]["changed_at"])
    return (last - first).total_seconds() <= 60


def _build_summary(session: list[dict]) -> tuple[str, str, str]:
    """Build summary text, tables_touched, and policy_uids from a session.

    Returns (summary, tables_touched, policy_uids).
    """
    # Count changes by (table, operation)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    policy_uids: set[str] = set()
    tables: set[str] = set()

    for e in session:
        key = (e["table_name"], e["operation"])
        counts[key] += 1
        tables.add(e["table_name"])
        uid = e.get("_policy_uid")
        if uid:
            policy_uids.add(uid)

    # Build summary parts
    parts = []
    for (table, op), count in sorted(counts.items()):
        verb = _SUMMARY_VERBS.get((table, op), f"{op.lower()} {table}")
        if table == "policies" and policy_uids:
            uid_str = ", ".join(sorted(policy_uids)[:5])
            if count > 1:
                parts.append(f"{verb} x{count} ({uid_str})")
            else:
                parts.append(f"{verb} {uid_str}")
        elif count > 1:
            parts.append(f"{verb} x{count}")
        else:
            parts.append(verb)

    summary = "; ".join(parts) if parts else "Data changes"
    tables_touched = ", ".join(sorted(tables))
    policy_uids_str = ", ".join(sorted(policy_uids))

    return summary, tables_touched, policy_uids_str


def _has_covering_activity(
    conn: sqlite3.Connection,
    client_id: int,
    session_date: str,
    session_start: datetime,
    session_end: datetime,
) -> bool:
    """Check if an activity_log entry exists covering this session window.

    An activity covers a session if it was created within ±30 minutes of
    the session window. A same-date activity alone is NOT sufficient —
    the user may have logged a 9am call but done separate policy work at 3pm.
    """
    window_start = (session_start - timedelta(minutes=30)).isoformat()
    window_end = (session_end + timedelta(minutes=30)).isoformat()

    # Check if any user-created activity was created within the session window
    # (±30 min).  Exclude system-generated activities (Milestone auto-logs).
    # outlook_sync and thread_inherit emails count as real work — they
    # represent genuine client contact and satisfy the covering-activity check.
    # Include `source IS NULL` for parity with anomaly_engine (pre-migration-122
    # rows legitimately have no source).
    count = conn.execute(
        """SELECT COUNT(*) FROM activity_log
           WHERE client_id = ?
           AND created_at >= ? AND created_at <= ?
           AND activity_type NOT IN ('Milestone')
           AND (source IN ('manual', 'outlook_sync', 'thread_inherit') OR source IS NULL)""",
        (client_id, window_start, window_end),
    ).fetchone()[0]

    return count > 0


def scan_for_unlogged_sessions(
    conn: sqlite3.Connection, start_date: str, end_date: str
) -> int:
    """Scan audit log and write unlogged sessions to suggested_activities.

    Args:
        conn: Database connection
        start_date: ISO date string (inclusive)
        end_date: ISO date string (inclusive)

    Returns:
        Number of new suggestions created.
    """
    gap_minutes = cfg.get("review_session_gap_minutes", 30)
    dismiss_days = cfg.get("review_dismiss_days", 7)

    # Expire old dismissals first
    expire_dismissed_suggestions(conn)

    # 1. Query audit log for tracked tables
    placeholders = ",".join("?" * len(_TRACKED_TABLES))
    rows = conn.execute(
        f"""SELECT table_name, row_id, operation, old_values, new_values, changed_at
            FROM audit_log
            WHERE table_name IN ({placeholders})
              AND date(changed_at) >= ? AND date(changed_at) <= ?
            ORDER BY changed_at""",
        list(_TRACKED_TABLES) + [start_date, end_date],
    ).fetchall()

    if not rows:
        return 0

    entries = [
        {
            "table_name": r[0],
            "row_id": r[1],
            "operation": r[2],
            "old_values": r[3],
            "new_values": r[4],
            "changed_at": r[5],
        }
        for r in rows
        if (r[0], r[2]) not in _SYSTEM_OPS  # skip system-generated ops
    ]

    if not entries:
        return 0

    # 2. Resolve to client_ids
    resolved = _resolve_client_ids(conn, entries)
    if not resolved:
        return 0

    # 3. Cluster into sessions
    client_sessions = _cluster_sessions(resolved, gap_minutes)

    # 4. Process each session
    created = 0
    for client_id, sessions in client_sessions.items():
        # Get client name for logging
        client_row = conn.execute(
            "SELECT name FROM clients WHERE id = ?", (client_id,)
        ).fetchone()
        client_name = client_row[0] if client_row else f"Client #{client_id}"

        for session in sessions:
            start_dt = datetime.fromisoformat(session[0]["changed_at"])
            end_dt = datetime.fromisoformat(session[-1]["changed_at"])
            session_date_str = start_dt.date().isoformat()

            # Duration: round up to 0.1h, minimum 0.1
            raw_hours = (end_dt - start_dt).total_seconds() / 3600
            duration = max(0.1, math.ceil(raw_hours * 10) / 10)

            # Check for bulk operation
            is_bulk = _is_bulk_operation(session)

            # Check for existing activity coverage
            if _has_covering_activity(conn, client_id, session_date_str, start_dt, end_dt):
                continue

            # Build summary
            summary, tables_touched, policy_uids = _build_summary(session)
            if is_bulk:
                summary = f"[Bulk Import] {summary}"

            # Determine status (bulk = auto-dismissed)
            status = "dismissed" if is_bulk else "pending"
            dismissed_at = datetime.now().isoformat() if is_bulk else None
            dismiss_expires = (
                (datetime.now() + timedelta(days=dismiss_days)).isoformat()
                if is_bulk
                else None
            )

            # INSERT OR IGNORE (unique constraint on client_id + session_start)
            try:
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO suggested_activities
                       (client_id, session_date, session_start, session_end,
                        estimated_duration_hours, tables_touched, change_count,
                        policy_uids, summary, status, dismissed_at, dismiss_expires_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        client_id,
                        session_date_str,
                        start_dt.isoformat(),
                        end_dt.isoformat(),
                        duration,
                        tables_touched,
                        len(session),
                        policy_uids,
                        summary,
                        status,
                        dismissed_at,
                        dismiss_expires,
                    ),
                )
                if cursor.rowcount > 0:
                    created += 1
            except Exception as e:
                logger.warning("Failed to insert suggested activity: %s", e)

    if created:
        conn.commit()
        logger.info("Activity review scan: %d new suggestions for %s to %s", created, start_date, end_date)

    return created


def get_pending_review_count(conn: sqlite3.Connection) -> int:
    """Get count of pending suggested activities."""
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM suggested_activities WHERE status = 'pending'"
        ).fetchone()[0]
    except Exception as e:
        logger.warning("Failed to get pending review count: %s", e)
        return 0


def get_pending_suggestions(conn: sqlite3.Connection) -> list[dict]:
    """Get all pending suggestions with client names, ordered by session_start."""
    try:
        rows = conn.execute(
            """SELECT sa.*, c.name as client_name
               FROM suggested_activities sa
               JOIN clients c ON c.id = sa.client_id
               WHERE sa.status = 'pending'
               ORDER BY sa.session_start DESC"""
        ).fetchall()
        # sqlite3.Row supports dict() conversion directly
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning("Failed to get pending suggestions: %s", e)
        return []


def expire_dismissed_suggestions(conn: sqlite3.Connection) -> int:
    """Reset expired dismissals back to pending.

    Returns number of suggestions reset.
    """
    try:
        now = datetime.now().isoformat()
        cursor = conn.execute(
            """UPDATE suggested_activities
               SET status = 'pending', dismissed_at = NULL, dismiss_expires_at = NULL
               WHERE status = 'dismissed'
                 AND dismiss_expires_at IS NOT NULL
                 AND dismiss_expires_at < ?""",
            (now,),
        )
        count = cursor.rowcount
        if count > 0:
            conn.commit()
            logger.info("Activity review: %d expired dismissals reset to pending", count)
        return count
    except Exception as e:
        logger.warning("Failed to expire dismissed suggestions: %s", e)
        return 0
