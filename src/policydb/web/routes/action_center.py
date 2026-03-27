"""Action Center — unified tabbed page for Follow-ups, Inbox, Activities, Scratchpads."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

from policydb.web.app import get_db, templates
import policydb.config as cfg
from policydb.activity_review import (
    expire_dismissed_suggestions,
    get_pending_review_count,
    get_pending_suggestions,
    scan_for_unlogged_sessions,
)
from policydb.queries import (
    get_activities,
    get_all_followups,
    get_dashboard_hours_this_month,
    get_suggested_followups,
    get_time_summary,
)

router = APIRouter()


# ── Nudge escalation ─────────────────────────────────────────────────────────


def _compute_nudge_tier(conn, policy_uid: str, dispositions: list[dict]) -> tuple[int, str]:
    """Count waiting_external activities for a policy in last 90 days.

    Returns (count, tier) where tier is 'normal', 'elevated', or 'urgent'.
    """
    waiting_labels = [
        d["label"] for d in dispositions
        if d.get("accountability") == "waiting_external"
    ]
    if not waiting_labels or not policy_uid:
        return 1, "normal"

    placeholders = ",".join("?" * len(waiting_labels))
    count = conn.execute(
        f"""SELECT COUNT(*) FROM activity_log
            WHERE policy_id = (SELECT id FROM policies WHERE policy_uid = ?)
              AND disposition IN ({placeholders})
              AND activity_date >= date('now', '-90 days')""",
        [policy_uid] + waiting_labels,
    ).fetchone()[0]

    count = max(count, 1)
    tier = "urgent" if count >= 3 else "elevated" if count >= 2 else "normal"
    return count, tier


# ── Classification ───────────────────────────────────────────────────────────


def _classify_item(item: dict, today: date, stale_threshold: int, dispositions: list[dict]) -> str:
    """Classify a follow-up item into a bucket.

    Returns one of: triage, today, overdue, stale, nudge_due, watching, scheduled
    """
    source = item.get("source", "activity")
    disposition = item.get("disposition") or ""
    fu_date_str = item.get("follow_up_date", "")

    # Step 1: Triage — activity items with no disposition
    # But only if the follow-up date is today or past. Future items without
    # a disposition go to "watching" — they were created early and aren't
    # actionable yet.
    if source in ("activity", "project") and not disposition.strip():
        try:
            _fu = date.fromisoformat(fu_date_str)
            if (today - _fu).days < 0:
                return "watching"
        except (ValueError, TypeError):
            pass
        return "triage"

    # Step 2: Map disposition → accountability
    accountability = "my_action"  # default
    for d in dispositions:
        if d.get("label", "").lower() == disposition.lower():
            accountability = d.get("accountability", "my_action")
            break

    # Step 3: Scheduled
    if accountability == "scheduled":
        return "scheduled"

    # Parse date
    try:
        fu_date = date.fromisoformat(fu_date_str)
    except (ValueError, TypeError):
        return "triage"  # bad date → triage

    days_overdue = (today - fu_date).days

    # Step 4: waiting_external
    if accountability == "waiting_external":
        return "nudge_due" if days_overdue >= 0 else "watching"

    # Step 5: my_action date tiers
    if days_overdue == 0:
        return "today"
    elif days_overdue > stale_threshold:
        return "stale"
    elif days_overdue > 0:
        return "overdue"
    else:
        return "watching"  # future my_action → watching with "my turn" badge


# ── Helpers ──────────────────────────────────────────────────────────────────


def _sidebar_ctx(conn) -> dict:
    """Compute sidebar stats, quick actions context, and recent activity feed."""
    overdue, upcoming = get_all_followups(conn, window=7)
    inbox_pending = conn.execute(
        "SELECT COUNT(*) FROM inbox WHERE status='pending'"
    ).fetchone()[0]
    hours_month = get_dashboard_hours_this_month(conn)
    # Due this week: items from upcoming whose follow_up_date <= 7 days out
    due_this_week = len(upcoming)
    # Recent activity feed (last 5)
    recent = [dict(r) for r in conn.execute("""
        SELECT a.id, a.activity_type, a.subject, a.activity_date, a.created_at,
               c.name AS client_name
        FROM activity_log a JOIN clients c ON a.client_id = c.id
        ORDER BY a.id DESC LIMIT 5
    """).fetchall()]
    return {
        "overdue_count": len(overdue),
        "due_this_week": due_this_week,
        "inbox_pending": inbox_pending,
        "hours_month": hours_month,
        "recent_activities": recent,
    }


def _followups_ctx(conn, window: int, activity_type: str, q: str,
                   client_id: int = 0) -> dict:
    """Build follow-ups tab context — reuses logic from activities.py.

    Produces 8 urgency/accountability buckets alongside the existing
    overdue/upcoming breakdown for backward compatibility:
      triage        – activity items with no disposition (need initial triage)
      today_bucket  – my_action items due today
      overdue_bucket– my_action items overdue (1..stale_threshold days)
      stale         – my_action items overdue beyond stale_threshold
      nudge_due     – waiting_external items with follow_up_date <= today
      prep_coming   – timeline milestones whose prep_alert_date has arrived
      watching      – future items (both my_action and waiting_external)
      scheduled     – items with 'scheduled' accountability
    """
    from policydb.web.routes.activities import _add_mailto_subjects

    filter_client_ids = [client_id] if client_id else None
    excluded = cfg.get("renewal_statuses_excluded", [])
    overdue_raw, upcoming_raw = get_all_followups(conn, window=window, client_ids=filter_client_ids)
    suggested = get_suggested_followups(conn, excluded_statuses=excluded, client_ids=filter_client_ids)

    if activity_type:
        overdue_raw = [r for r in overdue_raw if r.get("activity_type") == activity_type]
        upcoming_raw = [r for r in upcoming_raw if r.get("activity_type") == activity_type]
    if q:
        q_lower = q.lower()
        overdue_raw = [r for r in overdue_raw if q_lower in r.get("client_name", "").lower()]
        upcoming_raw = [r for r in upcoming_raw if q_lower in r.get("client_name", "").lower()]
        suggested = [r for r in suggested if q_lower in r.get("client_name", "").lower()]

    subject_tpl = cfg.get("email_subject_followup", "Re: {{client_name}} — {{policy_type}} — {{subject}}")
    overdue = _add_mailto_subjects(overdue_raw, subject_tpl)
    upcoming = _add_mailto_subjects(upcoming_raw, subject_tpl)

    today_str = date.today().isoformat()
    tomorrow_str = (date.today() + timedelta(days=1)).isoformat()
    today_items = [r for r in upcoming if r.get("follow_up_date") == today_str]
    tomorrow_items = [r for r in upcoming if r.get("follow_up_date") == tomorrow_str]
    later_items = [r for r in upcoming if r.get("follow_up_date", "") > tomorrow_str]

    # ── 7 accountability/urgency buckets ──────────────────────────────
    all_items = overdue + upcoming
    stale_threshold = cfg.get("stale_threshold_days", 14)
    dispositions = cfg.get("follow_up_dispositions", [])
    today = date.today()

    buckets: dict[str, list[dict]] = {
        "triage": [], "today": [], "overdue": [], "stale": [],
        "nudge_due": [], "watching": [], "scheduled": [],
    }

    for item in all_items:
        bucket = _classify_item(item, today, stale_threshold, dispositions)
        # Ensure days_overdue is computed
        fu_date_val = item.get("follow_up_date", "")
        try:
            d = date.fromisoformat(fu_date_val)
            item["days_overdue"] = (today - d).days
        except (ValueError, TypeError):
            item["days_overdue"] = 0
        # Compute days from follow-up date to expiration for proximity warnings
        exp_val = item.get("expiration_date") or ""
        if exp_val and fu_date_val:
            try:
                exp_d = date.fromisoformat(exp_val[:10])
                fu_d = date.fromisoformat(fu_date_val[:10])
                item["days_fu_to_expiry"] = (exp_d - fu_d).days
            except (ValueError, TypeError):
                item["days_fu_to_expiry"] = None
        else:
            item["days_fu_to_expiry"] = None
        # Mark future my_action items in watching with "my turn" badge
        if bucket == "watching":
            disp = (item.get("disposition") or "").lower()
            acct = "my_action"
            for dd in dispositions:
                if dd.get("label", "").lower() == disp:
                    acct = dd.get("accountability", "my_action")
                    break
            item["is_my_turn"] = (acct == "my_action")
        buckets[bucket].append(item)

    # Compute nudge escalation tiers for nudge_due items
    for item in buckets["nudge_due"]:
        count, tier = _compute_nudge_tier(conn, item.get("policy_uid"), dispositions)
        item["nudge_count"] = count
        item["escalation_tier"] = tier

    # ── Cadence computation ──────────────────────────────────────────
    # For activity/project source items with a disposition that has default_days,
    # compute how far over cadence they are:
    #   on_cadence: days_overdue <= default_days
    #   mild: 1-2x over default_days
    #   severe: 2x+ over default_days
    _disp_days_map = {}
    for d in dispositions:
        label = d.get("label", "").lower()
        dd = d.get("default_days", 0)
        if dd and dd > 0:
            _disp_days_map[label] = dd

    for bucket_items in buckets.values():
        for item in bucket_items:
            src = item.get("source", "")
            if src not in ("activity", "project"):
                continue
            disp_label = (item.get("disposition") or "").lower()
            default_days = _disp_days_map.get(disp_label, 0)
            if default_days <= 0:
                continue
            days_over = item.get("days_overdue", 0)
            if days_over <= default_days:
                item["cadence"] = "on_cadence"
            elif days_over <= default_days * 2:
                item["cadence"] = "mild"
            else:
                item["cadence"] = "severe"

    # Inject overdue milestones into urgency tiers.
    # Skip milestones that already have an activity_log follow-up to avoid
    # double-counting from the dual mandated-activity / timeline systems.
    _activity_policy_uids = {
        item.get("policy_uid")
        for item in all_items
        if item.get("source") == "activity" and item.get("policy_uid")
    }
    try:
        milestone_rows = conn.execute("""
            SELECT pt.policy_uid, pt.milestone_name, pt.projected_date,
                   pt.ideal_date, pt.health, pt.accountability, pt.completed_date,
                   p.policy_type, p.carrier, p.project_name, p.project_id,
                   c.name AS client_name, c.id AS client_id, c.cn_number
            FROM policy_timeline pt
            JOIN policies p ON p.policy_uid = pt.policy_uid
            JOIN clients c ON c.id = p.client_id
            WHERE pt.projected_date <= ?
              AND pt.completed_date IS NULL
            ORDER BY pt.projected_date
        """, (today_str,)).fetchall()

        for row in milestone_rows:
            item = dict(row)
            # Skip if an activity follow-up already covers this policy
            if item["policy_uid"] in _activity_policy_uids:
                continue
            item["source"] = "milestone"
            item["source_label"] = "Milestone"
            item["is_milestone"] = True
            item["follow_up_date"] = item["projected_date"]
            item["id"] = f"ms-{item['policy_uid']}-{item['milestone_name']}"
            try:
                days_past = (today - date.fromisoformat(item["projected_date"])).days
            except (ValueError, TypeError):
                days_past = 0
            item["days_overdue"] = days_past
            # Route through accountability classification
            ms_accountability = item.get("accountability") or "my_action"
            if ms_accountability == "waiting_external":
                buckets["nudge_due" if days_past >= 0 else "watching"].append(item)
            elif ms_accountability == "scheduled":
                buckets["scheduled"].append(item)
            elif days_past == 0:
                buckets["today"].append(item)
            elif days_past > stale_threshold:
                buckets["stale"].append(item)
            elif days_past > 0:
                buckets["overdue"].append(item)
    except Exception:
        pass  # policy_timeline may not exist yet

    # Prep coming — timeline milestones whose prep_alert_date has arrived
    # Only include milestones whose projected_date is still in the future
    prep_coming: list[dict] = []
    try:
        prep_rows = conn.execute("""
            SELECT pt.policy_uid, pt.milestone_name, pt.projected_date,
                   pt.prep_alert_date, pt.accountability, pt.health,
                   p.policy_type, p.carrier, p.project_name, p.project_id,
                   c.name AS client_name, c.id AS client_id, c.cn_number
            FROM policy_timeline pt
            JOIN policies p ON p.policy_uid = pt.policy_uid
            JOIN clients c ON c.id = p.client_id
            WHERE pt.prep_alert_date <= ? AND pt.projected_date > ?
              AND pt.completed_date IS NULL
              AND pt.prep_alert_date IS NOT NULL
            ORDER BY pt.projected_date
        """, (today_str, today_str)).fetchall()
        prep_coming = [dict(r) for r in prep_rows]
    except Exception:
        # policy_timeline table may not exist yet — degrade gracefully
        pass

    # Apply search filter to prep items
    if q:
        q_lower = q.lower()
        prep_coming = [r for r in prep_coming if q_lower in r.get("client_name", "").lower()]

    all_clients = [dict(c) for c in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]

    return {
        # Legacy buckets (backward compat for existing row templates)
        "overdue": overdue,
        "upcoming": upcoming,
        "today_items": today_items,
        "tomorrow_items": tomorrow_items,
        "later_items": later_items,
        "suggested": suggested,
        # Urgency-tier buckets (replaces act_now)
        "triage": buckets["triage"],
        "today_bucket": buckets["today"],
        "overdue_bucket": buckets["overdue"],
        "stale": buckets["stale"],
        # Backward compat — union of triage+today+overdue+stale for old template
        "act_now": buckets["triage"] + buckets["today"] + buckets["overdue"] + buckets["stale"],
        # Accountability buckets
        "nudge_due": buckets["nudge_due"],
        "prep_coming": prep_coming,
        "watching": buckets["watching"],
        "scheduled": buckets["scheduled"],
        # Filter state
        "window": window,
        "activity_type": activity_type,
        "q": q,
        "client_id": client_id,
        "all_clients": all_clients,
        "today": today_str,
        "activity_types": cfg.get("activity_types", []),
        "renewal_statuses": cfg.get("renewal_statuses", []),
        "dispositions": cfg.get("follow_up_dispositions", []),
        "followup_expiration_buffer_days": cfg.get("followup_expiration_buffer_days", 3),
    }


def _inbox_ctx(conn, show_processed: bool = False) -> dict:
    """Build inbox tab context."""
    pending = [dict(r) for r in conn.execute("""
        SELECT i.*, c.name AS client_name, c.cn_number, ct.name AS contact_name
        FROM inbox i LEFT JOIN clients c ON i.client_id = c.id
        LEFT JOIN contacts ct ON i.contact_id = ct.id
        WHERE i.status = 'pending'
        ORDER BY i.created_at DESC
    """).fetchall()]
    processed = []
    if show_processed:
        processed = [dict(r) for r in conn.execute("""
            SELECT i.*, c.name AS client_name, c.cn_number,
                   a.subject AS activity_subject, a.policy_uid AS activity_policy_uid,
                   p.project_id AS activity_project_id
            FROM inbox i LEFT JOIN clients c ON i.client_id = c.id
            LEFT JOIN activity_log a ON i.activity_id = a.id
            LEFT JOIN policies p ON a.policy_uid = p.policy_uid
            WHERE i.status = 'processed'
            ORDER BY i.processed_at DESC LIMIT 50
        """).fetchall()]
    all_clients = [dict(r) for r in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]
    return {
        "pending": pending,
        "processed": processed,
        "show_processed": show_processed,
        "all_clients": all_clients,
        "activity_types": cfg.get("activity_types", []),
        "dispositions": cfg.get("follow_up_dispositions", []),
    }


def _activities_ctx(conn, days: int = 90, activity_type: str = "",
                    client_id: int = 0, q: str = "") -> dict:
    """Build activities tab context."""
    from policydb.web.routes.activities import _attach_pc_emails

    rows = [dict(r) for r in get_activities(
        conn, days=days,
        client_id=client_id or None,
        activity_type=activity_type or None,
    )]
    _attach_pc_emails(conn, rows)

    # Apply text search filter
    if q:
        q_lower = q.lower()
        rows = [r for r in rows if (
            q_lower in r.get("subject", "").lower()
            or q_lower in r.get("client_name", "").lower()
            or q_lower in (r.get("details") or "").lower()
        )]

    time_summary = get_time_summary(
        conn, days=days,
        client_id=client_id or None,
        activity_type=activity_type or None,
    )
    all_clients = [dict(c) for c in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]
    dispositions = cfg.get("follow_up_dispositions", [])
    disposition_labels = [d["label"] if isinstance(d, dict) else d for d in dispositions]
    return {
        "activities": rows,
        "time_summary": time_summary,
        "days": days,
        "activity_type": activity_type,
        "client_id": client_id,
        "q": q,
        "activity_types": cfg.get("activity_types", []),
        "disposition_labels": disposition_labels,
        "all_clients": all_clients,
    }


def _scratchpads_ctx(conn) -> dict:
    """Aggregate non-empty scratchpads from all sources."""
    scratchpads = []
    # Dashboard
    dash = conn.execute("SELECT content, updated_at FROM user_notes WHERE id=1").fetchone()
    if dash and (dash["content"] or "").strip():
        scratchpads.append({
            "source": "dashboard", "label": "Dashboard", "link": "/",
            "content": dash["content"], "updated_at": dash["updated_at"],
        })
    # Client scratchpads
    for cs in conn.execute("""
        SELECT cs.client_id, cs.content, cs.updated_at, c.name AS client_name, c.cn_number
        FROM client_scratchpad cs JOIN clients c ON cs.client_id = c.id
        WHERE cs.content IS NOT NULL AND cs.content != ''
    """).fetchall():
        scratchpads.append({
            "source": "client", "label": cs["client_name"],
            "link": f"/clients/{cs['client_id']}",
            "content": cs["content"], "updated_at": cs["updated_at"],
            "client_id": cs["client_id"], "cn_number": cs["cn_number"],
        })
    # Policy scratchpads
    for ps in conn.execute("""
        SELECT ps.policy_uid, ps.content, ps.updated_at, p.policy_type,
               p.client_id, p.project_id, c.name AS client_name, c.cn_number
        FROM policy_scratchpad ps JOIN policies p ON ps.policy_uid = p.policy_uid
        JOIN clients c ON p.client_id = c.id
        WHERE ps.content IS NOT NULL AND ps.content != ''
    """).fetchall():
        scratchpads.append({
            "source": "policy",
            "label": f"{ps['client_name']} — {ps['policy_type']}",
            "link": f"/policies/{ps['policy_uid']}/edit",
            "content": ps["content"], "updated_at": ps["updated_at"],
            "client_id": ps["client_id"], "policy_uid": ps["policy_uid"],
            "cn_number": ps["cn_number"], "project_id": ps["project_id"],
        })
    return {"scratchpads": scratchpads}


def _portfolio_health_ctx(conn) -> dict:
    """Compute portfolio health counts by worst-health-per-policy from timeline."""
    counts = {"on_track": 0, "drifting": 0, "compressed": 0, "at_risk": 0, "critical": 0}
    try:
        rows = conn.execute("""
            SELECT policy_uid,
                   MIN(CASE health
                       WHEN 'critical' THEN 1 WHEN 'at_risk' THEN 2
                       WHEN 'compressed' THEN 3 WHEN 'drifting' THEN 4
                       ELSE 5 END) AS worst_rank
            FROM policy_timeline
            WHERE completed_date IS NULL
            GROUP BY policy_uid
        """).fetchall()
        rank_map = {1: "critical", 2: "at_risk", 3: "compressed", 4: "drifting", 5: "on_track"}
        for r in rows:
            h = rank_map.get(r["worst_rank"], "on_track")
            counts[h] = counts.get(h, 0) + 1
    except Exception:
        pass  # policy_timeline may not exist yet
    return {"health_counts": [(k, v) for k, v in counts.items()]}


def _risk_alerts_ctx(conn) -> dict:
    """Return at_risk / critical timeline items for the risk alerts banner."""
    alerts: list[dict] = []
    try:
        rows = conn.execute("""
            SELECT DISTINCT pt.policy_uid, pt.health, pt.waiting_on,
                   pt.acknowledged, pt.acknowledged_at,
                   p.policy_type, p.expiration_date,
                   c.name AS client_name, c.id AS client_id,
                   CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_expiry,
                   CAST(julianday(pt.projected_date) - julianday(pt.ideal_date) AS INTEGER) AS drift_days
            FROM policy_timeline pt
            JOIN policies p ON p.policy_uid = pt.policy_uid
            JOIN clients c ON c.id = p.client_id
            WHERE pt.health IN ('at_risk', 'critical')
              AND pt.completed_date IS NULL
            ORDER BY CASE pt.health WHEN 'critical' THEN 0 ELSE 1 END, p.expiration_date
        """).fetchall()
        alerts = [dict(r) for r in rows]
    except Exception:
        pass  # policy_timeline may not exist yet
    return {"risk_alerts": alerts}


# ── Issues context ───────────────────────────────────────────────────────────


def _issues_ctx(conn, q: str = "", client_id: int = 0) -> dict:
    """Build issues tab context — open and recently resolved issues."""
    today = date.today().isoformat()
    rows = conn.execute("""
        SELECT a.id, a.issue_uid, a.subject, a.details, a.client_id, a.policy_id,
               a.program_id, a.issue_status, a.issue_severity, a.issue_sla_days,
               a.resolution_type, a.resolution_notes, a.root_cause_category,
               a.resolved_date, a.activity_date, a.created_at,
               c.name AS client_name,
               p.policy_uid, p.policy_type, p.carrier,
               (SELECT COUNT(*) FROM activity_log sub
                WHERE sub.issue_id = a.id) AS activity_count,
               (SELECT COALESCE(SUM(sub.duration_hours), 0) FROM activity_log sub
                WHERE sub.issue_id = a.id AND sub.duration_hours IS NOT NULL) AS total_hours,
               (SELECT MAX(sub.activity_date) FROM activity_log sub
                WHERE sub.issue_id = a.id) AS last_activity_date,
               julianday(?) - julianday(a.activity_date) AS days_open
        FROM activity_log a
        LEFT JOIN clients c ON c.id = a.client_id
        LEFT JOIN policies p ON p.id = a.policy_id
        WHERE a.item_kind = 'issue'
          AND a.issue_id IS NULL
        ORDER BY
          CASE a.issue_severity
            WHEN 'Critical' THEN 0
            WHEN 'High' THEN 1
            WHEN 'Normal' THEN 2
            WHEN 'Low' THEN 3
            ELSE 4
          END,
          a.activity_date ASC
    """, (today,)).fetchall()
    issues = [dict(r) for r in rows]

    # Apply filters
    if client_id:
        issues = [i for i in issues if i.get("client_id") == client_id]
    if q:
        q_lower = q.lower()
        issues = [i for i in issues if
                  q_lower in (i.get("subject") or "").lower() or
                  q_lower in (i.get("client_name") or "").lower() or
                  q_lower in (i.get("details") or "").lower()]

    # Bucket into sections
    severities = cfg.get("issue_severities", [])
    sla_map = {s["label"]: s.get("sla_days", 7) for s in severities}

    critical_overdue = []
    active = []
    waiting = []
    recently_resolved = []

    for issue in issues:
        status = issue.get("issue_status") or "Open"
        severity = issue.get("issue_severity") or "Normal"
        days_open = issue.get("days_open") or 0
        sla = issue.get("issue_sla_days") or sla_map.get(severity, 7)
        issue["sla_days"] = sla
        issue["over_sla"] = days_open > sla

        if status in ("Resolved", "Closed"):
            # Only show recently resolved (last 7 days)
            if issue.get("resolved_date"):
                from dateutil.parser import parse as dparse
                try:
                    days_since = (date.today() - dparse(issue["resolved_date"]).date()).days
                    if days_since <= 7:
                        recently_resolved.append(issue)
                except Exception:
                    pass
        elif severity == "Critical" or issue["over_sla"]:
            critical_overdue.append(issue)
        elif status == "Waiting":
            waiting.append(issue)
        else:
            active.append(issue)

    open_count = len(critical_overdue) + len(active) + len(waiting)

    # All clients for filter dropdown
    all_clients = [dict(r) for r in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]

    return {
        "critical_overdue": critical_overdue,
        "active_issues": active,
        "waiting_issues": waiting,
        "recently_resolved": recently_resolved,
        "open_issues_count": open_count,
        "all_clients": all_clients,
        "q": q,
        "client_id": client_id,
        "issue_severities": cfg.get("issue_severities", []),
        "issue_lifecycle_states": cfg.get("issue_lifecycle_states", []),
    }


# ── Main page ────────────────────────────────────────────────────────────────


@router.get("/action-center", response_class=HTMLResponse)
def action_center_page(request: Request, tab: str = "", conn=Depends(get_db)):
    """Main Action Center page — renders shell with tabs and sidebar."""
    sidebar = _sidebar_ctx(conn)
    # Default tab content: follow-ups (loaded server-side for first render)
    initial_tab = tab or "followups"
    tab_ctx = {}
    if initial_tab == "followups":
        tab_ctx = _followups_ctx(conn, window=30, activity_type="", q="")
    elif initial_tab == "inbox":
        tab_ctx = _inbox_ctx(conn)
    elif initial_tab == "activities":
        tab_ctx = _activities_ctx(conn)
    elif initial_tab == "scratchpads":
        tab_ctx = _scratchpads_ctx(conn)
    elif initial_tab == "issues":
        tab_ctx = _issues_ctx(conn)
    elif initial_tab == "activity-review":
        tab_ctx = _activity_review_ctx(conn)
    # Always compute scratchpad count for tab badge
    scratchpad_count = 0
    if "scratchpads" not in tab_ctx:
        dash = conn.execute("SELECT content FROM user_notes WHERE id=1").fetchone()
        if dash and (dash["content"] or "").strip():
            scratchpad_count += 1
        scratchpad_count += conn.execute(
            "SELECT COUNT(*) FROM client_scratchpad WHERE content IS NOT NULL AND content != ''"
        ).fetchone()[0]
        scratchpad_count += conn.execute(
            "SELECT COUNT(*) FROM policy_scratchpad WHERE content IS NOT NULL AND content != ''"
        ).fetchone()[0]
    # Portfolio health + risk alerts (always shown)
    health_ctx = _portfolio_health_ctx(conn)
    risk_ctx = _risk_alerts_ctx(conn)
    # Accountability counts for sidebar badges
    act_now_count = (
        len(tab_ctx.get("triage", []))
        + len(tab_ctx.get("today_bucket", []))
        + len(tab_ctx.get("overdue_bucket", []))
        + len(tab_ctx.get("stale", []))
    )
    nudge_due_count = len(tab_ctx.get("nudge_due", []))
    review_pending_count = get_pending_review_count(conn)
    # Issues count for tab badge (skip if already computed)
    if "open_issues_count" in tab_ctx:
        issues_count = tab_ctx["open_issues_count"]
    else:
        issues_count = conn.execute(
            "SELECT COUNT(*) FROM activity_log WHERE item_kind='issue' AND issue_id IS NULL "
            "AND (issue_status IS NULL OR issue_status NOT IN ('Resolved','Closed'))"
        ).fetchone()[0]
    ctx = {
        "request": request,
        "active": "action-center",
        "initial_tab": initial_tab,
        "scratchpad_count": scratchpad_count,
        "act_now_count": act_now_count,
        "nudge_due_count": nudge_due_count,
        "review_pending_count": review_pending_count,
        "issues_count": issues_count,
        **sidebar,
        **tab_ctx,
        **health_ctx,
        **risk_ctx,
    }
    return templates.TemplateResponse("action_center/page.html", ctx)


# ── Tab partials (HTMX) ─────────────────────────────────────────────────────


@router.get("/action-center/followups", response_class=HTMLResponse)
def ac_followups(
    request: Request,
    window: int = 30,
    activity_type: str = "",
    q: str = "",
    client_id: int = 0,
    conn=Depends(get_db),
):
    ctx = _followups_ctx(conn, window, activity_type, q, client_id=client_id)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_followups.html", ctx)


@router.get("/action-center/inbox", response_class=HTMLResponse)
def ac_inbox(
    request: Request,
    show_processed: str = "",
    conn=Depends(get_db),
):
    ctx = _inbox_ctx(conn, show_processed=bool(show_processed))
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_inbox.html", ctx)


@router.get("/action-center/activities", response_class=HTMLResponse)
def ac_activities(
    request: Request,
    days: int = 90,
    activity_type: str = "",
    client_id: int = 0,
    q: str = "",
    conn=Depends(get_db),
):
    ctx = _activities_ctx(conn, days=days, activity_type=activity_type,
                          client_id=client_id, q=q)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_activities.html", ctx)


@router.get("/action-center/scratchpads", response_class=HTMLResponse)
def ac_scratchpads(request: Request, conn=Depends(get_db)):
    ctx = _scratchpads_ctx(conn)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_scratchpads.html", ctx)


@router.get("/action-center/issues", response_class=HTMLResponse)
def ac_issues(
    request: Request,
    q: str = "",
    client_id: int = 0,
    conn=Depends(get_db),
):
    ctx = _issues_ctx(conn, q=q, client_id=client_id)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_issues.html", ctx)


@router.get("/action-center/sidebar", response_class=HTMLResponse)
def ac_sidebar(request: Request, conn=Depends(get_db)):
    ctx = _sidebar_ctx(conn)
    health_ctx = _portfolio_health_ctx(conn)
    ctx.update(health_ctx)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_sidebar.html", ctx)


# ── Risk alert acknowledge ────────────────────────────────────────────────────


@router.post("/action-center/acknowledge/{policy_uid}", response_class=HTMLResponse)
def acknowledge_alert(request: Request, policy_uid: str, conn=Depends(get_db)):
    """Mark at_risk/critical timeline items as acknowledged for a policy."""
    now = datetime.now().isoformat()
    try:
        conn.execute("""
            UPDATE policy_timeline SET acknowledged = 1, acknowledged_at = ?
            WHERE policy_uid = ? AND health IN ('at_risk', 'critical')
        """, (now, policy_uid))
        conn.commit()
    except Exception:
        pass
    # Return the updated alert row
    try:
        row = conn.execute("""
            SELECT DISTINCT pt.policy_uid, pt.health, pt.waiting_on,
                   pt.acknowledged, pt.acknowledged_at,
                   p.policy_type, p.expiration_date,
                   c.name AS client_name, c.id AS client_id,
                   CAST(julianday(p.expiration_date) - julianday('now') AS INTEGER) AS days_to_expiry,
                   CAST(julianday(pt.projected_date) - julianday(pt.ideal_date) AS INTEGER) AS drift_days
            FROM policy_timeline pt
            JOIN policies p ON p.policy_uid = pt.policy_uid
            JOIN clients c ON c.id = p.client_id
            WHERE pt.policy_uid = ? AND pt.health IN ('at_risk', 'critical')
              AND pt.completed_date IS NULL
            LIMIT 1
        """, (policy_uid,)).fetchone()
        if row:
            alert = dict(row)
            return templates.TemplateResponse(
                "action_center/_risk_alert_row.html",
                {"request": request, "alert": alert},
            )
    except Exception:
        pass
    # Fallback: return empty (row disappears)
    return HTMLResponse("")


# ── Triage disposition ───────────────────────────────────────────────────────


@router.post("/policies/{policy_uid}/milestone/{milestone_name}/complete", response_class=HTMLResponse)
def complete_timeline_milestone_endpoint(
    request: Request,
    policy_uid: str,
    milestone_name: str,
    conn=Depends(get_db),
):
    """Complete a timeline milestone and re-render the followups tab."""
    import urllib.parse
    from policydb.timeline_engine import complete_timeline_milestone
    decoded_name = urllib.parse.unquote(milestone_name)
    complete_timeline_milestone(conn, policy_uid, decoded_name)
    conn.commit()
    # Re-render the full followups tab
    ctx = _followups_ctx(conn, window=30, activity_type="", q="")
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_followups.html", ctx)


@router.post("/action-center/set-disposition/{activity_id}", response_class=HTMLResponse)
def set_disposition(
    request: Request,
    activity_id: int,
    disposition: str = Form(""),
    conn=Depends(get_db),
):
    """Set disposition on a triage activity item and re-render followups tab."""
    if disposition:
        conn.execute(
            "UPDATE activity_log SET disposition = ? WHERE id = ?",
            (disposition, activity_id),
        )
        conn.commit()
    # Re-render the full followups tab so the item moves to its new section
    ctx = _followups_ctx(conn, window=30, activity_type="", q="")
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_followups.html", ctx)


# ── Activity Review ──────────────────────────────────────────────────────────

# Track last backfill completion (resets on server restart)
_backfill_done: bool = False


def _activity_review_ctx(conn, scan_date: str = "") -> dict:
    """Build context for the Activity Review tab."""
    global _backfill_done
    today = date.today().isoformat()
    target_date = scan_date or today

    if not scan_date:
        # Always scan today (picks up new work since last visit)
        scan_for_unlogged_sessions(conn, today, today)

        # On first visit after server start, also backfill last 7 days
        if not _backfill_done:
            lookback = (date.today() - timedelta(days=7)).isoformat()
            yesterday = (date.today() - timedelta(days=1)).isoformat()
            scan_for_unlogged_sessions(conn, lookback, yesterday)
            _backfill_done = True

    suggestions = get_pending_suggestions(conn)
    activity_types = cfg.get("activity_types", [])
    default_activity_type = cfg.get("default_review_activity_type", "Other")

    return {
        "suggestions": suggestions,
        "review_count": len(suggestions),
        "scan_date": target_date,
        "activity_types": activity_types,
        "default_activity_type": default_activity_type,
    }


@router.get("/action-center/activity-review", response_class=HTMLResponse)
def ac_activity_review(request: Request, conn=Depends(get_db)):
    """Activity Review tab partial — lazy loaded."""
    ctx = _activity_review_ctx(conn)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_activity_review.html", ctx)


@router.post("/action-center/activity-review/scan", response_class=HTMLResponse)
def ac_activity_review_scan(
    request: Request,
    scan_date: str = Form(""),
    conn=Depends(get_db),
):
    """Run activity review scan for a specific date."""
    target = scan_date or date.today().isoformat()
    scan_for_unlogged_sessions(conn, target, target)

    ctx = _activity_review_ctx(conn, scan_date=target)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_activity_review.html", ctx)


@router.post("/action-center/activity-review/{suggestion_id}/dismiss", response_class=HTMLResponse)
def ac_dismiss_suggestion(
    request: Request,
    suggestion_id: int,
    conn=Depends(get_db),
):
    """Soft-dismiss a suggested activity (resurfaces after configured days)."""
    dismiss_days = cfg.get("review_dismiss_days", 7)
    now = datetime.now()
    expires = (now + timedelta(days=dismiss_days)).isoformat()
    conn.execute(
        """UPDATE suggested_activities
           SET status = 'dismissed', dismissed_at = ?, dismiss_expires_at = ?
           WHERE id = ?""",
        (now.isoformat(), expires, suggestion_id),
    )
    conn.commit()

    # Return empty string to remove the card + OOB badge update
    count = get_pending_review_count(conn)
    badge_html = f'<span id="review-badge" hx-swap-oob="true">'
    if count:
        badge_html += f'<span class="tab-badge" style="background:#f59e0b;color:white">{count}</span>'
    badge_html += '</span>'
    return HTMLResponse(badge_html)


@router.post("/action-center/activity-review/{suggestion_id}/log", response_class=HTMLResponse)
def ac_log_suggestion(
    request: Request,
    suggestion_id: int,
    activity_type: str = Form(""),
    subject: str = Form(""),
    details: str = Form(""),
    duration_hours: float = Form(0.1),
    activity_date: str = Form(""),
    policy_uid: str = Form(""),
    conn=Depends(get_db),
):
    """Log a suggested activity as a real activity_log entry."""
    # Look up the suggestion to get client_id
    suggestion = conn.execute(
        "SELECT * FROM suggested_activities WHERE id = ?", (suggestion_id,)
    ).fetchone()
    if not suggestion:
        return HTMLResponse("")

    client_id = suggestion["client_id"]
    act_date = activity_date or suggestion["session_date"]

    # Resolve policy_id from policy_uid if provided
    policy_id = None
    if policy_uid:
        prow = conn.execute(
            "SELECT id FROM policies WHERE policy_uid = ?", (policy_uid,)
        ).fetchone()
        if prow:
            policy_id = prow["id"]

    # Insert activity
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, subject,
            details, duration_hours, account_exec, follow_up_done)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'Grant', 0)""",
        (act_date, client_id, policy_id, activity_type or "Other",
         subject or "Account work (from review)", details, duration_hours),
    )
    activity_id = cursor.lastrowid

    # Mark suggestion as logged
    conn.execute(
        """UPDATE suggested_activities
           SET status = 'logged', logged_activity_id = ?
           WHERE id = ?""",
        (activity_id, suggestion_id),
    )
    conn.commit()

    # Return empty + OOB badge update
    count = get_pending_review_count(conn)
    badge_html = f'<span id="review-badge" hx-swap-oob="true">'
    if count:
        badge_html += f'<span class="tab-badge" style="background:#f59e0b;color:white">{count}</span>'
    badge_html += '</span>'
    return HTMLResponse(badge_html)
