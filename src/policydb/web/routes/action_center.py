"""Action Center — unified tabbed page for Follow-ups, Inbox, Activities, Scratchpads."""

from __future__ import annotations

from datetime import date, timedelta

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
    """Build follow-ups tab context — reuses logic from activities.py."""
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

    all_clients = [dict(c) for c in conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()]

    return {
        "overdue": overdue,
        "upcoming": upcoming,
        "today_items": today_items,
        "tomorrow_items": tomorrow_items,
        "later_items": later_items,
        "suggested": suggested,
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
    ctx = {
        "request": request,
        "active": "action-center",
        "initial_tab": initial_tab,
        "scratchpad_count": scratchpad_count,
        **sidebar,
        **tab_ctx,
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
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_sidebar.html", ctx)
