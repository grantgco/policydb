"""Phase 4 — Timesheet Review core module.

Builds the payload for the weekly timesheet review page. All flag
computation is live: no materialized views, no background jobs.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any


def _daterange(start: date, end: date) -> list[date]:
    """Inclusive list of dates from start to end."""
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _classify_range(start: date, end: date) -> str:
    if start == end:
        return "day"
    days = (end - start).days + 1
    if days == 7 and start.weekday() == 0:
        return "week"
    return "range"


def build_timesheet_payload(
    conn: sqlite3.Connection,
    *,
    start: date,
    end: date,
) -> dict[str, Any]:
    """Build the full timesheet-review payload for the given range.

    Returns a dict with keys: range, totals, flags, days, closeout.
    """
    days = [
        {
            "date": d.isoformat(),
            "label": d.strftime("%a · %b %-d"),
            "total_hours": 0.0,
            "is_low": False,
            "activities": [],
        }
        for d in _daterange(start, end)
    ]

    return {
        "range": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "label": f"{start.strftime('%b %-d')} – {end.strftime('%b %-d')}",
            "kind": _classify_range(start, end),
        },
        "totals": {
            "total_hours": 0.0,
            "activity_count": 0,
            "flag_count": 0,
        },
        "flags": {
            "low_days": [],
            "silent_clients": [],
            "unreviewed_emails": 0,
            "null_hour_activities": 0,
        },
        "days": days,
        "closeout": {"closed_at": None, "snapshot": None},
    }
