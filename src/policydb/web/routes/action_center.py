"""Action Center — unified tabbed page for Follow-ups, Inbox, Activities, Scratchpads."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from policydb.web.app import get_db, templates
import policydb.config as cfg
from policydb.queries import (
    get_activities,
    get_all_followups,
    get_dashboard_hours_this_month,
    get_suggested_followups,
    get_time_summary,
)

router = APIRouter()


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

    Produces 5 accountability buckets alongside the existing overdue/upcoming
    breakdown for backward compatibility:
      act_now       – my_action items due today or overdue
      nudge_due     – waiting_external items with follow_up_date <= today
      prep_coming   – timeline milestones whose prep_alert_date has arrived
      watching      – waiting_external items with follow_up_date > today
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

    # ── 5 accountability buckets ──────────────────────────────────────
    all_items = overdue + upcoming
    act_now: list[dict] = []
    nudge_due: list[dict] = []
    watching: list[dict] = []
    scheduled: list[dict] = []

    for item in all_items:
        acc = item.get("accountability", "my_action")
        fu_date = item.get("follow_up_date") or ""
        if acc == "scheduled":
            scheduled.append(item)
        elif acc == "waiting_external":
            if fu_date <= today_str:
                nudge_due.append(item)
            else:
                watching.append(item)
        else:  # my_action or unknown
            act_now.append(item)

    # Compute nudge escalation tiers for nudge_due items
    for item in nudge_due:
        thread_id = item.get("thread_id")
        if thread_id:
            count = conn.execute(
                "SELECT COUNT(*) FROM activity_log WHERE thread_id = ?",
                (thread_id,),
            ).fetchone()[0]
            item["nudge_count"] = count
            item["escalation_tier"] = (
                "urgent" if count >= 3 else "elevated" if count >= 2 else "normal"
            )
        else:
            item["nudge_count"] = 1
            item["escalation_tier"] = "normal"

    # Prep coming — timeline milestones whose prep_alert_date has arrived
    prep_coming: list[dict] = []
    try:
        prep_rows = conn.execute("""
            SELECT pt.policy_uid, pt.milestone_name, pt.projected_date,
                   pt.prep_alert_date, pt.accountability, pt.health,
                   p.policy_type, c.name AS client_name, c.id AS client_id
            FROM policy_timeline pt
            JOIN policies p ON p.policy_uid = pt.policy_uid
            JOIN clients c ON c.id = p.client_id
            WHERE pt.prep_alert_date <= ? AND pt.completed_date IS NULL
              AND pt.prep_alert_date IS NOT NULL
            ORDER BY pt.projected_date
        """, (today_str,)).fetchall()
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
        # New accountability buckets
        "act_now": act_now,
        "nudge_due": nudge_due,
        "prep_coming": prep_coming,
        "watching": watching,
        "scheduled": scheduled,
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
    }


def _inbox_ctx(conn, show_processed: bool = False) -> dict:
    """Build inbox tab context."""
    pending = [dict(r) for r in conn.execute("""
        SELECT i.*, c.name AS client_name, ct.name AS contact_name
        FROM inbox i LEFT JOIN clients c ON i.client_id = c.id
        LEFT JOIN contacts ct ON i.contact_id = ct.id
        WHERE i.status = 'pending'
        ORDER BY i.created_at DESC
    """).fetchall()]
    processed = []
    if show_processed:
        processed = [dict(r) for r in conn.execute("""
            SELECT i.*, c.name AS client_name, a.subject AS activity_subject
            FROM inbox i LEFT JOIN clients c ON i.client_id = c.id
            LEFT JOIN activity_log a ON i.activity_id = a.id
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
        SELECT cs.client_id, cs.content, cs.updated_at, c.name AS client_name
        FROM client_scratchpad cs JOIN clients c ON cs.client_id = c.id
        WHERE cs.content IS NOT NULL AND cs.content != ''
    """).fetchall():
        scratchpads.append({
            "source": "client", "label": cs["client_name"],
            "link": f"/clients/{cs['client_id']}",
            "content": cs["content"], "updated_at": cs["updated_at"],
            "client_id": cs["client_id"],
        })
    # Policy scratchpads
    for ps in conn.execute("""
        SELECT ps.policy_uid, ps.content, ps.updated_at, p.policy_type,
               p.client_id, c.name AS client_name
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
    act_now_count = len(tab_ctx.get("act_now", []))
    nudge_due_count = len(tab_ctx.get("nudge_due", []))
    ctx = {
        "request": request,
        "active": "action-center",
        "initial_tab": initial_tab,
        "scratchpad_count": scratchpad_count,
        "act_now_count": act_now_count,
        "nudge_due_count": nudge_due_count,
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
