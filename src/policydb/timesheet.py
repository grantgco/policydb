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
    """Fetch all activity_log rows whose activity_date is in [start, end], joined to client name."""
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """SELECT a.id, a.activity_date, a.activity_type, a.subject,
                  a.duration_hours, a.reviewed_at, a.source, a.follow_up_done,
                  a.item_kind, a.client_id, a.policy_id, a.details,
                  c.name AS client_name
           FROM activity_log a
           LEFT JOIN clients c ON a.client_id = c.id
           WHERE a.activity_date BETWEEN ? AND ?
           ORDER BY a.activity_date, a.id""",
        (start.isoformat(), end.isoformat()),
    ).fetchall()


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
            "client_id": r["client_id"],
            "client_name": r["client_name"],
            "policy_id": r["policy_id"],
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
            "flag_count": len(low_days),
        },
        "flags": {
            "low_days": low_days,
            "silent_clients": [],
            "unreviewed_emails": 0,
            "null_hour_activities": 0,
        },
        "days": list(days_map.values()),
        "closeout": {"closed_at": None, "snapshot": None},
    }
