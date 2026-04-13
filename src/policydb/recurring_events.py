"""Recurring events: lazy generation of repeating issue instances.

A recurring_events row is a schedule template. For each active template
whose next_occurrence is within the generation horizon,
generate_due_recurring_instances materializes an activity_log row
(item_kind='issue', issue_status='Open') and advances the template's
next_occurrence by one cadence step.

Completion/resolution goes through the standard /issues/{id}/resolve
endpoint; a hook (advance_template_for_completion) re-runs the generator
so the next occurrence appears immediately after the user resolves an
instance.
"""

from __future__ import annotations

import logging
import sqlite3
from calendar import monthrange
from datetime import date, datetime, timedelta

import policydb.config as cfg
from policydb.db import generate_issue_uid

logger = logging.getLogger("policydb.recurring_events")


# ─────────────────────────────────────────────────────────────────────────
# UID generation — mirrors next_policy_uid() in db.py:2517
# ─────────────────────────────────────────────────────────────────────────

def next_recurring_uid(conn: sqlite3.Connection) -> str:
    """Generate next REC-NNN uid using the atomic uid_sequence table.

    Falls back to SELECT MAX for databases that haven't run migration 144.
    """
    try:
        conn.execute(
            """
            UPDATE uid_sequence
            SET next_val = CASE
                WHEN (
                    SELECT COALESCE(MAX(CAST(SUBSTR(recurring_uid, 5) AS INTEGER)), 0)
                    FROM recurring_events WHERE recurring_uid LIKE 'REC-%'
                ) > next_val
                THEN (
                    SELECT COALESCE(MAX(CAST(SUBSTR(recurring_uid, 5) AS INTEGER)), 0)
                    FROM recurring_events WHERE recurring_uid LIKE 'REC-%'
                )
                ELSE next_val
            END
            WHERE prefix = 'REC'
            """
        )
        conn.execute(
            "UPDATE uid_sequence SET next_val = next_val + 1 WHERE prefix = 'REC'"
        )
        row = conn.execute(
            "SELECT next_val FROM uid_sequence WHERE prefix = 'REC'"
        ).fetchone()
        if row is not None:
            return f"REC-{row[0]:03d}"
    except Exception:
        pass
    # Fallback
    row = conn.execute(
        "SELECT recurring_uid FROM recurring_events WHERE recurring_uid LIKE 'REC-%' "
        "ORDER BY CAST(SUBSTR(recurring_uid, 5) AS INTEGER) DESC LIMIT 1"
    ).fetchone()
    if not row or not row["recurring_uid"]:
        return "REC-001"
    try:
        n = int(row["recurring_uid"].split("-")[1]) + 1
    except (IndexError, ValueError):
        n = 1
    return f"REC-{n:03d}"


# ─────────────────────────────────────────────────────────────────────────
# Date arithmetic — pure functions, no config or DB I/O
# ─────────────────────────────────────────────────────────────────────────

def _parse(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()


def _snap_dow(d: date, day_of_week: int | None) -> date:
    """Snap forward to the next occurrence of day_of_week (0=Mon..6=Sun)."""
    if day_of_week is None:
        return d
    delta = (day_of_week - d.weekday()) % 7
    return d + timedelta(days=delta)


def _add_months(d: date, months: int, day_of_month: int | None) -> date:
    """Add months, clamping to month-end for day_of_month > 28 in short months."""
    total = d.month - 1 + months
    y = d.year + total // 12
    m = total % 12 + 1
    target_day = day_of_month if day_of_month else d.day
    last_day = monthrange(y, m)[1]
    return date(y, m, min(target_day, last_day))


def _advance(
    anchor: date,
    cadence: str,
    interval_n: int,
    day_of_week: int | None,
    day_of_month: int | None,
) -> date:
    """Advance anchor by exactly one cadence step."""
    n = max(1, int(interval_n or 1))
    cadence_norm = (cadence or "").strip()

    if cadence_norm == "Daily":
        return anchor + timedelta(days=n)
    if cadence_norm == "Weekly":
        return _snap_dow(anchor + timedelta(weeks=n), day_of_week)
    if cadence_norm == "Biweekly":
        return _snap_dow(anchor + timedelta(weeks=2 * n), day_of_week)
    if cadence_norm == "Monthly":
        return _add_months(anchor, 1 * n, day_of_month)
    if cadence_norm == "Quarterly":
        return _add_months(anchor, 3 * n, day_of_month)
    if cadence_norm == "Semi-Annual":
        return _add_months(anchor, 6 * n, day_of_month)
    if cadence_norm == "Annual":
        return _add_months(anchor, 12 * n, day_of_month)

    logger.warning("Unknown cadence %r; defaulting to +7 days", cadence_norm)
    return anchor + timedelta(days=7)


# ─────────────────────────────────────────────────────────────────────────
# Generator — called from init_db and from build_focus_queue
# ─────────────────────────────────────────────────────────────────────────

def generate_due_recurring_instances(
    conn: sqlite3.Connection, today: date | None = None
) -> int:
    """Materialize activity_log issue rows for every template whose
    next_occurrence falls within the generation horizon.

    Idempotent: safe to call repeatedly on the same day, on startup, and on
    every Focus Queue build. Returns number of instance rows inserted.
    """
    today = today or date.today()
    horizon_days = int(cfg.get("recurring_event_generation_horizon_days", 14))
    max_catchup = int(cfg.get("recurring_event_max_catchup", 12))
    horizon = today + timedelta(days=horizon_days)

    rows = conn.execute(
        """
        SELECT re.*
        FROM recurring_events re
        JOIN clients c ON re.client_id = c.id
        WHERE re.active = 1
          AND c.archived = 0
          AND re.next_occurrence <= ?
          AND (re.end_date IS NULL OR re.end_date >= ?)
        """,
        (horizon.isoformat(), today.isoformat()),
    ).fetchall()

    severities_cfg = cfg.get("issue_severities", []) or []
    inserted = 0
    for raw in rows:
        r = dict(raw)
        try:
            inserted += _materialize_template(
                conn, r, today, horizon, max_catchup, severities_cfg
            )
        except Exception:
            logger.exception(
                "Recurring event generation failed for id=%s", r.get("id")
            )

    if inserted:
        conn.commit()
        logger.info(
            "Recurring events: materialized %d issue instance(s)", inserted
        )
    return inserted


def _sla_for_severity(severities_cfg: list, severity: str) -> int:
    for sev in severities_cfg:
        if sev.get("label") == severity:
            try:
                return int(sev.get("sla_days", 7))
            except (TypeError, ValueError):
                return 7
    return 7


def _materialize_template(
    conn: sqlite3.Connection,
    r: dict,
    today: date,
    horizon: date,
    max_catchup: int,
    severities_cfg: list,
) -> int:
    """Insert zero or more issue rows for a single template. Advance pointer."""
    count = 0
    next_occ = _parse(r["next_occurrence"]) or today
    end_date = _parse(r.get("end_date"))
    start_date = _parse(r.get("start_date")) or today
    mode = r.get("catch_up_mode") or "collapse"
    lead_days = int(r.get("lead_days") or 0)
    severity = r.get("default_severity") or "Normal"
    sla_days = _sla_for_severity(severities_cfg, severity)
    subject = r.get("subject_template") or r.get("name") or "Recurring event"
    details = r.get("details_template") or ""
    activity_type = "Issue"

    # Don't materialize before start_date
    if next_occ < start_date:
        next_occ = start_date

    # Collapse catch-up: if many occurrences are in the past, fast-forward
    # the pointer to the most recent one <= today and emit only one row.
    if mode == "collapse" and next_occ < today:
        while True:
            candidate = _advance(
                next_occ,
                r["cadence"],
                r["interval_n"],
                r.get("day_of_week"),
                r.get("day_of_month"),
            )
            if candidate > today:
                break
            next_occ = candidate

    # Emit rows for every next_occ within [-∞, horizon], bounded by max_catchup
    last_inserted_date = None
    while next_occ <= horizon and count < max_catchup:
        if end_date is not None and next_occ > end_date:
            break

        # Idempotency: skip insert if a row already exists for this
        # (template, instance_date) pair. Still advance the pointer.
        already = conn.execute(
            "SELECT 1 FROM activity_log WHERE recurring_event_id = ? AND recurring_instance_date = ? LIMIT 1",
            (r["id"], next_occ.isoformat()),
        ).fetchone()

        if not already:
            fu_date = next_occ - timedelta(days=lead_days)
            uid = generate_issue_uid()
            ae = r.get("account_exec") or cfg.get("default_account_exec", "") or ""
            conn.execute(
                """
                INSERT INTO activity_log (
                    activity_date, client_id, policy_id, activity_type, subject, details,
                    item_kind, issue_uid, issue_status, issue_severity, issue_sla_days,
                    due_date, account_exec, recurring_event_id, recurring_instance_date,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'issue', ?, 'Open', ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    today.isoformat(),
                    r["client_id"],
                    r.get("policy_id"),
                    activity_type,
                    subject,
                    details,
                    uid,
                    severity,
                    sla_days,
                    fu_date.isoformat(),
                    ae,
                    r["id"],
                    next_occ.isoformat(),
                ),
            )
            count += 1
            last_inserted_date = next_occ

        next_occ = _advance(
            next_occ,
            r["cadence"],
            r["interval_n"],
            r.get("day_of_week"),
            r.get("day_of_month"),
        )

    # Persist advanced pointer and last_generated_date
    conn.execute(
        "UPDATE recurring_events SET next_occurrence = ?, last_generated_date = ? WHERE id = ?",
        (
            next_occ.isoformat(),
            last_inserted_date.isoformat() if last_inserted_date else r.get("last_generated_date"),
            r["id"],
        ),
    )
    return count


# ─────────────────────────────────────────────────────────────────────────
# Completion hook — called from /issues/{id}/resolve
# ─────────────────────────────────────────────────────────────────────────

def advance_template_for_completion(
    conn: sqlite3.Connection, activity_id: int
) -> None:
    """If the resolved activity is a recurring instance, re-run the generator
    so the next occurrence materializes immediately. Safe no-op otherwise."""
    try:
        row = conn.execute(
            "SELECT recurring_event_id FROM activity_log WHERE id = ?",
            (activity_id,),
        ).fetchone()
    except sqlite3.OperationalError:
        # activity_log.recurring_event_id doesn't exist yet (pre-migration 144)
        return
    if not row or not row["recurring_event_id"]:
        return
    generate_due_recurring_instances(conn)


# ─────────────────────────────────────────────────────────────────────────
# Template CRUD helpers (used by route module)
# ─────────────────────────────────────────────────────────────────────────

def compute_initial_next_occurrence(
    start_date: date,
    cadence: str,
    day_of_week: int | None,
    day_of_month: int | None,
) -> date:
    """Snap start_date forward to the first valid occurrence based on DOW/DOM."""
    cadence_norm = (cadence or "").strip()
    if cadence_norm in ("Weekly", "Biweekly") and day_of_week is not None:
        return _snap_dow(start_date, day_of_week)
    if cadence_norm in ("Monthly", "Quarterly", "Semi-Annual", "Annual") and day_of_month:
        last_day = monthrange(start_date.year, start_date.month)[1]
        target = date(start_date.year, start_date.month, min(day_of_month, last_day))
        if target < start_date:
            return _add_months(start_date, 1, day_of_month)
        return target
    return start_date
