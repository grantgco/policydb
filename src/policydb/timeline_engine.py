"""Proactive Timeline Engine — generates and scores policy renewal timelines.

This module owns all timeline logic:
- generate_policy_timelines(conn) — build timeline rows for eligible policies
- get_policy_timeline(conn, policy_uid) — retrieve timeline for a single policy
- compute_health(...) — evaluate health status of a milestone
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import policydb.config as cfg


# ── Timeline Generation ────────────────────────────────────────────────


def _resolve_profile(milestone_profile_value: str) -> list[str]:
    """Return the list of milestone names for a given profile.

    Falls back to Simple Renewal for empty or unknown profile names.
    """
    profiles = cfg.get("milestone_profiles", [])
    profile_name = (milestone_profile_value or "").strip()
    if not profile_name:
        profile_name = "Simple Renewal"

    for p in profiles:
        if p["name"] == profile_name:
            return list(p.get("milestones", []))

    # Unknown profile — fall back to Simple Renewal
    for p in profiles:
        if p["name"] == "Simple Renewal":
            return list(p.get("milestones", []))

    return []


def _calculate_milestone_date(
    trigger: str, days: int, effective_date: date, expiration_date: date
) -> date:
    """Compute the target date based on trigger type."""
    if trigger == "days_before_expiry":
        return expiration_date - timedelta(days=days)
    elif trigger == "days_after_effective":
        return effective_date + timedelta(days=days)
    else:
        # Default to days_before_expiry for unknown triggers
        return expiration_date - timedelta(days=days)


def _should_include_activity(
    activity: dict, profile_milestones: list[str]
) -> bool:
    """Determine whether a mandated activity belongs in the timeline.

    Activities with a checklist_milestone are always included (core renewal
    process steps). Activities without one (RSM Meeting, Post-Binding Meeting,
    Client Presentation) are only included if their name appears in the
    profile's milestone list.
    """
    if activity.get("checklist_milestone"):
        return True
    return activity["name"] in profile_milestones


def generate_policy_timelines(conn) -> None:
    """Generate timeline rows for all eligible policies.

    Eligible = active, non-opportunity, non-archived, not a child in a program.
    For each eligible policy, resolves the milestone profile, then inserts
    timeline rows for mandated activities within the horizon window.
    """
    horizon_days = cfg.get("mandated_activity_horizon_days", 180)
    today = date.today()
    horizon_limit = today + timedelta(days=horizon_days)

    mandated = cfg.get("mandated_activities", [])

    rows = conn.execute("""
        SELECT policy_uid, effective_date, expiration_date,
               milestone_profile, program_id, is_program
        FROM policies
        WHERE (is_opportunity = 0 OR is_opportunity IS NULL)
          AND (archived = 0 OR archived IS NULL)
          AND expiration_date IS NOT NULL
          AND effective_date IS NOT NULL
    """).fetchall()

    for pol in rows:
        # Skip child policies in a program (they inherit from the parent)
        if pol["program_id"] is not None:
            continue

        policy_uid = pol["policy_uid"]
        eff_date = _parse_date(pol["effective_date"])
        exp_date = _parse_date(pol["expiration_date"])
        if eff_date is None or exp_date is None:
            continue

        profile_milestones = _resolve_profile(pol["milestone_profile"] or "")

        for activity in mandated:
            if not _should_include_activity(activity, profile_milestones):
                continue

            ideal = _calculate_milestone_date(
                activity["trigger"], activity["days"], eff_date, exp_date
            )

            # Skip if ideal date is in the past
            if ideal < today:
                continue

            # Skip if ideal date is beyond the horizon
            if ideal > horizon_limit:
                continue

            prep_days = activity.get("prep_days", 0)
            prep_alert = ideal - timedelta(days=prep_days) if prep_days else ideal

            conn.execute("""
                INSERT OR IGNORE INTO policy_timeline
                    (policy_uid, milestone_name, ideal_date, projected_date, prep_alert_date)
                VALUES (?, ?, ?, ?, ?)
            """, (
                policy_uid,
                activity["name"],
                ideal.isoformat(),
                ideal.isoformat(),
                prep_alert.isoformat(),
            ))

    conn.commit()


def get_policy_timeline(conn, policy_uid: str) -> list[dict]:
    """Retrieve all timeline rows for a given policy, ordered by ideal_date."""
    rows = conn.execute("""
        SELECT id, policy_uid, milestone_name, ideal_date, projected_date,
               completed_date, prep_alert_date, accountability, waiting_on,
               health, acknowledged, acknowledged_at, created_at
        FROM policy_timeline
        WHERE policy_uid = ?
        ORDER BY ideal_date
    """, (policy_uid,)).fetchall()
    return [dict(r) for r in rows]


# ── Health Computation ─────────────────────────────────────────────────


def compute_health(
    projected_date: date,
    ideal_date: date,
    completed_date: Optional[date],
    expiration_date: date,
    is_critical_milestone: bool,
    original_spacing: int,
    current_spacing: int,
    drift_threshold: int = 7,
    compression_threshold: float = 0.5,
) -> str:
    """Evaluate the health status of a single timeline milestone.

    Evaluation order (first match wins):
        1. Completed → on_track
        2. Critical milestone + expiration ≤30 days away → critical
        3. Projected date is past or <7 days away → at_risk
        4. Current spacing < 50% of original → compressed
        5. Drift from ideal > threshold → drifting
        6. Otherwise → on_track

    Returns one of: critical, at_risk, compressed, drifting, on_track
    """
    today = date.today()

    # 1. Completed milestones are always on_track
    if completed_date is not None:
        return "on_track"

    # 2. Critical milestone near expiration
    days_to_expiry = (expiration_date - today).days
    if is_critical_milestone and days_to_expiry <= 30:
        return "critical"

    # 3. Projected date is past or imminent (<7 days)
    days_to_projected = (projected_date - today).days
    if days_to_projected < 7:
        return "at_risk"

    # 4. Compressed — spacing between milestones squeezed
    if original_spacing > 0 and current_spacing < (original_spacing * compression_threshold):
        return "compressed"

    # 5. Drifting — projected has moved away from ideal
    drift = abs((projected_date - ideal_date).days)
    if drift > drift_threshold:
        return "drifting"

    # 6. Default
    return "on_track"


# ── Helpers ────────────────────────────────────────────────────────────


def _parse_date(val) -> Optional[date]:
    """Parse a date from a string or return None."""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    try:
        parts = str(val).split("-")
        return date(int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return None
