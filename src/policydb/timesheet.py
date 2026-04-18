"""Phase 4 — Timesheet Review core module.

Builds the payload for the weekly timesheet review page. All flag
computation is live: no materialized views, no background jobs.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from policydb import config as cfg


def _daterange(start: date, end: date) -> list[date]:
    """Inclusive list of dates from start to end."""
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _classify_range(start: date, end: date) -> str:
    """Classify a date range as 'day' (single), 'week' (Mon-Sun), or 'range' (arbitrary)."""
    if start == end:
        return "day"
    days = (end - start).days + 1
    if days == 7 and start.weekday() == 0:
        return "week"
    return "range"


def _load_activities(conn, start: date, end: date) -> list[sqlite3.Row]:
    """Fetch activity rows in [start, end], joined to client/policy/project/issue labels."""
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """SELECT a.id, a.activity_date, a.activity_type, a.subject,
                  a.duration_hours, a.reviewed_at, a.source, a.follow_up_done,
                  a.item_kind, a.client_id, a.policy_id, a.project_id, a.issue_id,
                  a.details,
                  c.name       AS client_name,
                  p.policy_uid AS policy_uid,
                  p.policy_type AS policy_type,
                  pr.name      AS project_name,
                  iss.issue_uid AS issue_uid,
                  iss.subject  AS issue_subject
           FROM activity_log a
           LEFT JOIN clients      c  ON c.id  = a.client_id
           LEFT JOIN policies     p  ON p.id  = a.policy_id
           LEFT JOIN projects     pr ON pr.id = a.project_id
           LEFT JOIN activity_log iss ON iss.id = a.issue_id
                                    AND iss.item_kind = 'issue'
           WHERE a.activity_date BETWEEN ? AND ?
           ORDER BY a.activity_date, a.id""",
        (start.isoformat(), end.isoformat()),
    ).fetchall()


def _compute_silent_clients(
    conn: sqlite3.Connection,
    start: date,
    end: date,
    renewal_window_days: int,
) -> list[dict[str, Any]]:
    """Clients with signals of active work but zero activity in the range.

    Signals: open followup (activity_log.item_kind='followup' AND follow_up_done=0),
             policy with expiration within renewal_window_days,
             open issue (activity_log.item_kind='issue' AND follow_up_done=0).
    """
    conn.row_factory = sqlite3.Row
    today = date.today().isoformat()
    window_end = (date.today() + timedelta(days=renewal_window_days)).isoformat()

    rows = conn.execute(
        """
        WITH candidates AS (
            SELECT DISTINCT client_id, 'open_followup' AS reason
            FROM activity_log
            WHERE item_kind = 'followup'
              AND follow_up_done = 0
              AND client_id IS NOT NULL
            UNION
            SELECT DISTINCT client_id, 'imminent_renewal' AS reason
            FROM policies
            WHERE expiration_date BETWEEN ? AND ?
              AND (is_opportunity = 0 OR is_opportunity IS NULL)
            UNION
            SELECT DISTINCT client_id, 'open_issue' AS reason
            FROM activity_log
            WHERE item_kind = 'issue'
              AND follow_up_done = 0
              AND client_id IS NOT NULL
        )
        SELECT c.id AS client_id, c.name, MIN(cand.reason) AS reason
        FROM candidates cand
        JOIN clients c ON c.id = cand.client_id
        LEFT JOIN activity_log a
               ON a.client_id = cand.client_id
              AND a.activity_date BETWEEN ? AND ?
              AND (a.duration_hours IS NOT NULL OR a.item_kind = 'activity')
        WHERE a.id IS NULL
        GROUP BY c.id, c.name
        ORDER BY c.name
        """,
        (today, window_end, start.isoformat(), end.isoformat()),
    ).fetchall()

    return [
        {
            "client_id": r["client_id"],
            "name": r["name"],
            "reason": r["reason"],
            "href": f"/clients/{r['client_id']}",
        }
        for r in rows
    ]


def build_timesheet_payload(
    conn: sqlite3.Connection,
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Build the full timesheet-review payload for the given range.

    Returns a dict with keys: range, totals, flags, days, closeout.
    """
    thresholds = cfg.get("timesheet_thresholds", {}) or {}
    low_threshold = float(thresholds.get("low_day_threshold_hours", 4.0))

    rows = _load_activities(conn, start, end)
    today = date.today()

    days_map: dict[str, dict[str, Any]] = {}
    for d in _daterange(start, end):
        iso = d.isoformat()
        days_map[iso] = {
            "date": iso,
            "label": d.strftime("%a · %b %-d"),
            "total_hours": 0.0,
            "is_low": False,
            "activities": [],
        }

    total_hours = 0.0
    for r in rows:
        day = days_map.get(r["activity_date"])
        if day is None:
            continue
        hrs = float(r["duration_hours"] or 0.0)
        day["total_hours"] = round(day["total_hours"] + hrs, 2)
        day["activities"].append({
            "id": r["id"],
            "subject": r["subject"] or "",
            "activity_type": r["activity_type"] or "",
            "duration_hours": r["duration_hours"],
            "reviewed_at": r["reviewed_at"],
            "source": r["source"] or "manual",
            "item_kind": r["item_kind"],

            "client_id": r["client_id"],
            "client_name": r["client_name"],
            "client_href": (
                f"/clients/{r['client_id']}" if r["client_id"] else None
            ),

            "policy_id": r["policy_id"],
            "policy_uid": r["policy_uid"],
            "policy_type": r["policy_type"],
            "policy_href": (
                f"/policies/{r['policy_uid']}/edit" if r["policy_uid"] else None
            ),

            "project_id": r["project_id"],
            "project_name": r["project_name"],
            "project_href": (
                f"/clients/{r['client_id']}/projects/{r['project_id']}"
                if r["project_id"] and r["client_id"] else None
            ),

            "issue_id": r["issue_id"],
            "issue_uid": r["issue_uid"],
            "issue_subject": r["issue_subject"],
            "issue_href": (
                f"/issues/{r['issue_uid']}" if r["issue_uid"] else None
            ),
        })
        total_hours += hrs

    low_days: list[str] = []
    for iso, day in days_map.items():
        d_obj = date.fromisoformat(iso)
        is_weekday = d_obj.weekday() < 5
        is_past_or_today = d_obj <= today
        has_activity = day["total_hours"] > 0
        if is_weekday and is_past_or_today and has_activity and day["total_hours"] < low_threshold:
            day["is_low"] = True
            low_days.append(iso)

    silence_window = int(thresholds.get("silence_renewal_window_days", 30))
    silent_clients = _compute_silent_clients(conn, start, end, silence_window)

    unreviewed_emails = conn.execute(
        """SELECT COUNT(*) AS n FROM activity_log
           WHERE reviewed_at IS NULL
             AND source IN ('outlook_sync', 'thread_inherit')
             AND activity_date BETWEEN ? AND ?""",
        (start.isoformat(), end.isoformat()),
    ).fetchone()["n"]

    null_hour_activities = conn.execute(
        """SELECT COUNT(*) AS n FROM activity_log
           WHERE duration_hours IS NULL
             AND activity_date BETWEEN ? AND ?""",
        (start.isoformat(), end.isoformat()),
    ).fetchone()["n"]

    flag_count = (
        len(low_days)
        + len(silent_clients)
        + (1 if unreviewed_emails else 0)
        + (1 if null_hour_activities else 0)
    )

    closeout = {"closed_at": None, "snapshot": None}
    if _classify_range(start, end) == "week":
        row = conn.execute(
            """SELECT closed_at, total_hours, activity_count, flag_count
               FROM timesheet_closeouts WHERE week_start = ?""",
            (start.isoformat(),),
        ).fetchone()
        if row:
            closeout = {
                "closed_at": row["closed_at"],
                "snapshot": {
                    "total_hours": row["total_hours"],
                    "activity_count": row["activity_count"],
                    "flag_count": row["flag_count"],
                },
            }

    return {
        "range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": f"{start.strftime('%b %-d')} – {end.strftime('%b %-d')}",
            "kind": _classify_range(start, end),
        },
        "totals": {
            "total_hours": round(total_hours, 2),
            "activity_count": len(rows),
            "flag_count": flag_count,
        },
        "flags": {
            "low_days": low_days,
            "silent_clients": silent_clients,
            "unreviewed_emails": unreviewed_emails,
            "null_hour_activities": null_hour_activities,
        },
        "days": list(days_map.values()),
        "closeout": closeout,
    }
