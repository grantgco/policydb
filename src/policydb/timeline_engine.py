"""Proactive Timeline Engine — generates and scores policy renewal timelines.

This module owns all timeline logic:
- generate_policy_timelines(conn) — build timeline rows for eligible policies
- get_policy_timeline(conn, policy_uid) — retrieve timeline for a single policy
- compute_health(...) — evaluate health status of a milestone
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
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


def generate_policy_timelines(conn, policy_uid: str | None = None) -> None:
    """Generate timeline rows for eligible policies.

    If *policy_uid* is provided, deletes existing rows and regenerates only
    that one policy's timeline.  Otherwise processes all eligible policies.

    Eligible = active, non-opportunity, non-archived, not a child in a program.
    For each eligible policy, resolves the milestone profile, then inserts
    timeline rows for mandated activities within the horizon window.
    """
    horizon_days = cfg.get("mandated_activity_horizon_days", 180)
    today = date.today()
    horizon_limit = today + timedelta(days=horizon_days)

    mandated = cfg.get("mandated_activities", [])

    if policy_uid:
        # Single-policy regeneration
        conn.execute("DELETE FROM policy_timeline WHERE policy_uid = ?", (policy_uid,))
        rows = conn.execute("""
            SELECT policy_uid, effective_date, expiration_date,
                   milestone_profile, program_id
            FROM policies
            WHERE policy_uid = ?
              AND (is_opportunity = 0 OR is_opportunity IS NULL)
              AND (archived = 0 OR archived IS NULL)
              AND expiration_date IS NOT NULL
              AND effective_date IS NOT NULL
        """, (policy_uid,)).fetchall()
    else:
        # Full regeneration: clean up stale uncompleted milestones before re-inserting.
        # Preserve rows that the user has actively triaged (waiting_on set,
        # accountability changed from default) and completed milestones.
        conn.execute("""
            DELETE FROM policy_timeline
            WHERE completed_date IS NULL
              AND (accountability IS NULL OR accountability = 'my_action')
              AND (waiting_on IS NULL OR waiting_on = '')
        """)
        # Also remove milestones for policies whose expiration already passed
        conn.execute("""
            DELETE FROM policy_timeline
            WHERE completed_date IS NULL
              AND policy_uid IN (
                  SELECT policy_uid FROM policies
                  WHERE expiration_date < date('now', '-30 days')
              )
        """)
        rows = conn.execute("""
            SELECT policy_uid, effective_date, expiration_date,
                   milestone_profile, program_id
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

        uid = pol["policy_uid"]
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
                uid,
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


# ── Recalculation Logic ────────────────────────────────────────────────


def recalculate_downstream(
    conn,
    policy_uid: str,
    changed_milestone: str,
    new_projected: str,
    expiration_date: str,
) -> list[dict]:
    """Shift projected dates for all milestones at or after the changed one.

    Algorithm:
    1. Read all timeline rows ordered by ideal_date.
    2. Find the changed milestone; update its projected_date to new_projected.
    3. For each downstream milestone (those after the changed one in ideal order):
       - original_gap = ideal[M] - ideal[M-1]
       - new_gap = max(original_gap, minimum_gap_days)
       - new_projected[M] = prev_projected + new_gap
       - Clamp to expiration_date if the new projected would exceed it.
    4. Persist all changes to DB.
    5. Call _recompute_prep_and_health() to refresh prep_alert_date and health.
    6. Return list of {milestone_name, old_projected, new_projected} for rows
       whose projected_date actually changed.
    """
    minimum_gap_days = cfg.get("timeline_engine", {}).get("minimum_gap_days", 3)
    exp_date = _parse_date(expiration_date)

    rows = conn.execute("""
        SELECT id, milestone_name, ideal_date, projected_date
        FROM policy_timeline
        WHERE policy_uid = ?
        ORDER BY ideal_date
    """, (policy_uid,)).fetchall()

    rows = [dict(r) for r in rows]

    # Locate the changed milestone index
    changed_idx = None
    for i, r in enumerate(rows):
        if r["milestone_name"] == changed_milestone:
            changed_idx = i
            break

    if changed_idx is None:
        # Milestone not found — nothing to do
        return []

    changes: list[dict] = []

    # Update the changed milestone
    old_projected = rows[changed_idx]["projected_date"]
    rows[changed_idx]["projected_date"] = new_projected
    if old_projected != new_projected:
        changes.append({
            "milestone_name": changed_milestone,
            "old_projected": old_projected,
            "new_projected": new_projected,
        })

    # Propagate downstream
    for i in range(changed_idx + 1, len(rows)):
        prev_ideal = _parse_date(rows[i - 1]["ideal_date"])
        curr_ideal = _parse_date(rows[i]["ideal_date"])
        original_gap = (curr_ideal - prev_ideal).days if (curr_ideal and prev_ideal) else minimum_gap_days
        new_gap = max(original_gap, minimum_gap_days)

        prev_projected = _parse_date(rows[i - 1]["projected_date"])
        if not prev_projected:
            continue  # Skip if previous projected date is unparseable
        new_proj = prev_projected + timedelta(days=new_gap)

        # Clamp to expiration_date
        if exp_date and new_proj > exp_date:
            new_proj = exp_date

        old_proj = rows[i]["projected_date"]
        new_proj_str = new_proj.isoformat()
        rows[i]["projected_date"] = new_proj_str

        if old_proj != new_proj_str:
            changes.append({
                "milestone_name": rows[i]["milestone_name"],
                "old_projected": old_proj,
                "new_projected": new_proj_str,
            })

    # Persist all rows from changed_idx onward
    for i in range(changed_idx, len(rows)):
        conn.execute("""
            UPDATE policy_timeline
            SET projected_date = ?
            WHERE id = ?
        """, (rows[i]["projected_date"], rows[i]["id"]))

    conn.commit()

    # Recompute prep_alert_date and health for all rows
    _recompute_prep_and_health(conn, policy_uid, expiration_date)

    return changes


def _recompute_prep_and_health(conn, policy_uid: str, expiration_date: str) -> None:
    """Recompute prep_alert_date and health for every timeline row of a policy.

    For each row:
    - Looks up prep_days from the matching mandated_activities config entry.
    - Sets prep_alert_date = projected_date - prep_days.
    - Computes spacing to the next milestone.
    - Calls compute_health() to derive new health status.
    - Updates the row in the DB.
    """
    mandated = cfg.get("mandated_activities", [])
    te_cfg = cfg.get("timeline_engine", {})
    drift_threshold = te_cfg.get("drift_threshold_days", 7)
    compression_threshold = te_cfg.get("compression_threshold", 0.5)

    exp_date = _parse_date(expiration_date)

    # Build lookup: milestone name → prep_days
    prep_days_map: dict[str, int] = {}
    for act in mandated:
        prep_days_map[act["name"]] = act.get("prep_days", 0)

    rows = conn.execute("""
        SELECT id, milestone_name, ideal_date, projected_date, completed_date,
               accountability, waiting_on, health, acknowledged
        FROM policy_timeline
        WHERE policy_uid = ?
        ORDER BY ideal_date
    """, (policy_uid,)).fetchall()
    rows = [dict(r) for r in rows]

    for idx, row in enumerate(rows):
        projected = _parse_date(row["projected_date"])
        ideal = _parse_date(row["ideal_date"])
        completed = _parse_date(row["completed_date"]) if row["completed_date"] else None

        if projected is None or ideal is None:
            continue

        # prep_alert_date
        prep_days = prep_days_map.get(row["milestone_name"], 0)
        prep_alert = projected - timedelta(days=prep_days) if prep_days else projected

        # Spacing: compare to next milestone
        if idx + 1 < len(rows):
            next_projected = _parse_date(rows[idx + 1]["projected_date"])
            next_ideal = _parse_date(rows[idx + 1]["ideal_date"])
            current_spacing = (next_projected - projected).days if next_projected else 0
            original_spacing = (next_ideal - ideal).days if next_ideal else 0
        else:
            # Last milestone — spacing to expiration
            current_spacing = (exp_date - projected).days if exp_date else 0
            original_spacing = (exp_date - ideal).days if exp_date else 0

        is_critical = prep_days_map.get(row["milestone_name"], 0) >= 3  # heuristic

        new_health = compute_health(
            projected_date=projected,
            ideal_date=ideal,
            completed_date=completed,
            expiration_date=exp_date or projected,
            is_critical_milestone=is_critical,
            original_spacing=max(original_spacing, 0),
            current_spacing=max(current_spacing, 0),
            drift_threshold=drift_threshold,
            compression_threshold=compression_threshold,
        )

        conn.execute("""
            UPDATE policy_timeline
            SET prep_alert_date = ?, health = ?
            WHERE id = ?
        """, (prep_alert.isoformat(), new_health, row["id"]))

    conn.commit()

    # Sync renewal issue severity from updated health
    from policydb.renewal_issues import sync_renewal_issue_severity
    sync_renewal_issue_severity(conn, policy_uid)
    conn.commit()


# ── Follow-up / Re-diary Integration ──────────────────────────────────


def update_timeline_from_followup(
    conn,
    policy_uid: str,
    milestone_name: str,
    disposition: str,
    new_followup_date: Optional[str],
    waiting_on: Optional[str] = None,
) -> None:
    """Update timeline when a follow-up is re-diaried with a disposition.

    Looks up the accountability state for the given disposition from config,
    updates the milestone's accountability and waiting_on fields, and — when
    the disposition maps to waiting_external — shifts the projected_date
    forward to new_followup_date and recalculates downstream milestones.
    """
    dispositions = cfg.get("follow_up_dispositions", [])
    accountability = "my_action"
    for d in dispositions:
        if d["label"] == disposition:
            accountability = d.get("accountability", "my_action")
            break

    # Update the milestone's accountability and waiting_on
    conn.execute("""
        UPDATE policy_timeline
        SET accountability = ?, waiting_on = ?
        WHERE policy_uid = ? AND milestone_name = ?
    """, (accountability, waiting_on, policy_uid, milestone_name))

    # If waiting_external, extend projected_date and recalculate downstream
    if accountability == "waiting_external" and new_followup_date:
        expiration = conn.execute(
            "SELECT expiration_date FROM policies WHERE policy_uid = ?", (policy_uid,)
        ).fetchone()
        if expiration and expiration["expiration_date"]:
            recalculate_downstream(
                conn, policy_uid, milestone_name,
                new_followup_date, expiration["expiration_date"]
            )

    conn.commit()


# ── Milestone Completion ───────────────────────────────────────────────


def complete_timeline_milestone(conn, policy_uid: str, milestone_name: str) -> None:
    """Mark a timeline milestone as completed. Also syncs to policy_milestones checklist.

    If the mandated_activities config maps this milestone to a checklist_milestone,
    the corresponding policy_milestones row is also marked completed. Finally,
    health is recomputed for the entire policy timeline.
    """
    now = datetime.now().isoformat()
    conn.execute("""
        UPDATE policy_timeline SET completed_date = ?
        WHERE policy_uid = ? AND milestone_name = ? AND completed_date IS NULL
    """, (now, policy_uid, milestone_name))

    # Find matching checklist_milestone from config
    activities = cfg.get("mandated_activities", [])
    checklist_name = None
    for act in activities:
        if act["name"] == milestone_name:
            checklist_name = act.get("checklist_milestone")
            break

    # If there's a matching checklist milestone, mark it done too
    if checklist_name:
        existing = conn.execute("""
            SELECT id, completed FROM policy_milestones
            WHERE policy_uid = ? AND milestone = ?
        """, (policy_uid, checklist_name)).fetchone()
        if existing and not existing["completed"]:
            conn.execute("""
                UPDATE policy_milestones SET completed = 1, completed_at = ?
                WHERE id = ?
            """, (now, existing["id"]))

    # Auto-close follow-ups that match this milestone by subject
    try:
        from rapidfuzz import fuzz
        policy_row = conn.execute(
            "SELECT id FROM policies WHERE policy_uid = ?", (policy_uid,)
        ).fetchone()
        if policy_row:
            open_fus = conn.execute("""
                SELECT id, subject FROM activity_log
                WHERE policy_id = ? AND follow_up_done = 0
                  AND follow_up_date IS NOT NULL AND auto_close_reason IS NULL
            """, (policy_row["id"],)).fetchall()
            for fu in open_fus:
                fu_subj = (fu["subject"] or "").lower()
                if fuzz.ratio(fu_subj, milestone_name.lower()) >= 80:
                    conn.execute("""
                        UPDATE activity_log
                        SET follow_up_done = 1,
                            auto_close_reason = 'milestone_completed',
                            auto_closed_at = ?,
                            auto_closed_by = 'complete_timeline_milestone'
                        WHERE id = ?
                    """, (now, fu["id"]))
    except ImportError:
        pass  # rapidfuzz not available — skip fuzzy matching

    # Recompute health (also triggers renewal issue severity sync)
    exp = conn.execute(
        "SELECT expiration_date FROM policies WHERE policy_uid = ?", (policy_uid,)
    ).fetchone()
    if exp and exp["expiration_date"]:
        _recompute_prep_and_health(conn, policy_uid, exp["expiration_date"])

    # Auto-resolve renewal issue if ALL milestones are now complete
    remaining = conn.execute("""
        SELECT COUNT(*) AS cnt FROM policy_timeline
        WHERE policy_uid = ? AND completed_date IS NULL
    """, (policy_uid,)).fetchone()
    if remaining and remaining["cnt"] == 0:
        from policydb.renewal_issues import auto_resolve_renewal_issue
        auto_resolve_renewal_issue(conn, policy_uid=policy_uid)

    conn.commit()


# ── Helpers ────────────────────────────────────────────────────────────


def suggest_profile(conn, policy_uid: str | None = None) -> dict[str, str]:
    """Return {policy_uid: suggested_profile_name} for policies without a profile.

    Uses ``milestone_profile_rules`` from config to suggest profiles based on
    premium thresholds.  Only considers active, non-opportunity, non-archived
    policies that are not children in a program.
    """
    rules = cfg.get("milestone_profile_rules", [])
    default_profile = "Simple Renewal"

    where = """
        WHERE (milestone_profile IS NULL OR milestone_profile = '')
          AND (is_opportunity = 0 OR is_opportunity IS NULL)
          AND (archived = 0 OR archived IS NULL)
          AND (program_id IS NULL OR program_id = '')
    """
    params: list = []
    if policy_uid:
        where += " AND policy_uid = ?"
        params.append(policy_uid)

    rows = conn.execute(
        f"SELECT policy_uid, premium FROM policies {where}", params  # noqa: S608
    ).fetchall()

    suggestions: dict[str, str] = {}
    for row in rows:
        premium = row["premium"] or 0
        profile = default_profile
        for rule in rules:
            gte = rule.get("if_premium_gte")
            lt = rule.get("if_premium_lt")
            if gte is not None and premium >= gte:
                profile = rule.get("suggest_profile", default_profile)
                break
            if lt is not None and premium < lt:
                profile = rule.get("suggest_profile", default_profile)
                break
        suggestions[row["policy_uid"]] = profile
    return suggestions


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
