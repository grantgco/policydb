"""Dashboard and search routes."""

from __future__ import annotations

import re
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from policydb import config as cfg
from policydb.queries import (
    get_all_followups,
    get_dashboard_hours_this_month,
    get_escalation_alerts,
    get_open_opportunities,
    get_renewal_metrics,
    attach_renewal_issues,
    attach_open_issues,
    get_dashboard_issues_widget,
    get_renewal_pipeline,
    get_stale_renewals,
    get_suggested_followups,
    full_text_search,
)
from policydb.web.app import get_db, templates

router = APIRouter()

URGENCY_ORDER = ["EXPIRED", "URGENT", "WARNING", "UPCOMING", "OK"]


def _attach_client_ids(conn, rows: list[dict]) -> list[dict]:
    result = []
    for d in rows:
        client_row = conn.execute(
            "SELECT id FROM clients WHERE name = ?", (d["client_name"],)
        ).fetchone()
        d["client_id"] = client_row["id"] if client_row else 0
        result.append(d)
    return result


@router.get("/dashboard/pipeline", response_class=HTMLResponse)
def dashboard_pipeline(request: Request, window: int = 90, status: str = "", conn=Depends(get_db)):
    """HTMX partial: pipeline table for dashboard window/status filter."""
    from policydb.web.routes.policies import _attach_milestone_progress, _attach_readiness_score
    excluded = cfg.get("renewal_statuses_excluded", [])
    rows = get_renewal_pipeline(conn, window_days=window, renewal_status=status or None, excluded_statuses=excluded)
    pipeline = _attach_readiness_score(conn, _attach_milestone_progress(
        conn, _attach_client_ids(conn, [dict(p) for p in rows])
    ))
    attach_renewal_issues(conn, pipeline)
    suggested_uids = {r["policy_uid"] for r in get_suggested_followups(conn, excluded_statuses=excluded)}
    return templates.TemplateResponse("policies/_pipeline_table.html", {
        "request": request,
        "pipeline": pipeline,
        "window": window,
        "status": status,
        "renewal_statuses": cfg.get("renewal_statuses"),
        "suggested_uids": suggested_uids,
    })


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, conn=Depends(get_db)):
    from policydb.web.routes.policies import _attach_milestone_progress, _attach_readiness_score
    excluded = cfg.get("renewal_statuses_excluded", [])
    # Stale cleanup — keep dashboard in sync with Action Center
    try:
        from policydb.queries import auto_close_stale_followups
        auto_close_stale_followups(conn)
    except Exception:
        pass
    metrics = get_renewal_metrics(conn)
    pipeline = get_renewal_pipeline(conn, window_days=90, excluded_statuses=excluded)
    overdue, upcoming = get_all_followups(conn, window=30)

    from policydb.email_templates import followup_context, render_tokens as _render_tokens
    _subj_tpl = cfg.get("email_subject_followup", cfg.get("email_subject_policy", "Re: {{client_name}} — {{policy_type}}"))
    for row in overdue + upcoming:
        row["mailto_subject"] = _render_tokens(_subj_tpl, followup_context(row))

    urgent_count = metrics.get("URGENT", {}).get("count", 0) + metrics.get("EXPIRED", {}).get("count", 0)
    urgency_breakdown = [(u, metrics.get(u, {"count": 0, "premium": 0})) for u in URGENCY_ORDER]

    pipeline_dicts = _attach_readiness_score(conn, _attach_milestone_progress(
        conn, _attach_client_ids(conn, [dict(p) for p in pipeline])
    ))
    attach_renewal_issues(conn, pipeline_dicts)

    # Readiness counts for summary card
    readiness_counts = {"critical": 0, "at_risk": 0, "on_track": 0, "ready": 0}
    for p in pipeline_dicts:
        label = p.get("readiness_label", "")
        if label == "CRITICAL":
            readiness_counts["critical"] += 1
        elif label == "AT RISK":
            readiness_counts["at_risk"] += 1
        elif label == "ON TRACK":
            readiness_counts["on_track"] += 1
        elif label == "READY":
            readiness_counts["ready"] += 1

    # Escalation alerts (replaces stale)
    escalation_alerts = _attach_client_ids(conn, get_escalation_alerts(conn, excluded_statuses=excluded))

    stale = _attach_client_ids(conn, [dict(r) for r in get_stale_renewals(conn, excluded_statuses=excluded)])
    suggested_uids = {r["policy_uid"] for r in get_suggested_followups(conn, excluded_statuses=excluded)}
    open_opportunities = get_open_opportunities(conn)
    _today = date.today()
    for o in open_opportunities:
        if o.get("target_effective_date"):
            try:
                o["days_to_target"] = (date.fromisoformat(o["target_effective_date"]) - _today).days
            except ValueError:
                o["days_to_target"] = None
        else:
            o["days_to_target"] = None
    _opp_subj_tpl = cfg.get("email_subject_policy", "Re: {{client_name}} — {{policy_type}}")
    for o in open_opportunities:
        _opp_ctx = {
            "client_name": o.get("client_name") or "",
            "policy_type": o.get("policy_type") or "",
            "carrier": o.get("carrier") or "",
            "policy_uid": o.get("policy_uid") or "",
            "project_name": (o.get("project_name") or "").strip(),
            "project_name_sep": f" \u2014 {o['project_name']}" if o.get("project_name") else "",
        }
        o["mailto_subject"] = _render_tokens(_opp_subj_tpl, _opp_ctx)

    attach_open_issues(conn, open_opportunities)
    issues_widget = get_dashboard_issues_widget(conn, limit=3)

    hours_this_month = get_dashboard_hours_this_month(conn)
    note_row = conn.execute("SELECT content, updated_at FROM user_notes WHERE id=1").fetchone()

    upcoming_meetings = [dict(r) for r in conn.execute(
        """SELECT cm.id, cm.title, cm.meeting_date, cm.meeting_time, cm.meeting_type, cm.phase,
                  c.name as client_name
           FROM client_meetings cm
           JOIN clients c ON c.id = cm.client_id
           WHERE cm.meeting_date >= date('now') AND cm.phase != 'complete'
           ORDER BY cm.meeting_date ASC, cm.meeting_time ASC
           LIMIT 3""",
    ).fetchall()]
    scratchpad_content = note_row["content"] if note_row else ""
    scratchpad_updated = note_row["updated_at"] if note_row else ""

    recent_client_notes = [dict(r) for r in conn.execute(
        """SELECT c.id AS client_id, c.name AS client_name,
                  cs.content, cs.updated_at
           FROM client_scratchpad cs
           JOIN clients c ON cs.client_id = c.id
           WHERE cs.content != ''
           ORDER BY cs.updated_at DESC LIMIT 5"""
    ).fetchall()]

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "active": "dashboard",
        "today": date.today().isoformat(),
        "metrics": metrics,
        "pipeline": pipeline_dicts,
        "overdue": overdue,
        "upcoming": upcoming,
        "dispositions": cfg.get("follow_up_dispositions", []),
        "urgent_count": urgent_count,
        "urgency_breakdown": urgency_breakdown,
        "renewal_statuses": cfg.get("renewal_statuses"),
        "dash_window": 90,
        "dash_status": "",
        "scratchpad_content": scratchpad_content,
        "scratchpad_updated": scratchpad_updated,
        "recent_client_notes": recent_client_notes,
        "stale": stale,
        "escalation_alerts": escalation_alerts,
        "readiness_counts": readiness_counts,
        "suggested_uids": suggested_uids,
        "open_opportunities": open_opportunities,
        "issues_widget": issues_widget,
        "hours_this_month": hours_this_month,
        "upcoming_meetings": upcoming_meetings,
    })


@router.post("/dashboard/scratchpad")
def save_scratchpad(request: Request, content: str = Form(""), conn=Depends(get_db)):
    """Auto-save global dashboard scratchpad. Returns JSON if Accept header requests it."""
    conn.execute(
        "INSERT INTO user_notes (id, content) VALUES (1, ?) "
        "ON CONFLICT(id) DO UPDATE SET content=excluded.content",
        (content,),
    )
    conn.commit()
    row = conn.execute("SELECT updated_at FROM user_notes WHERE id=1").fetchone()
    if "application/json" in (request.headers.get("accept") or ""):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        return JSONResponse({"ok": True, "saved_at": now})
    return templates.TemplateResponse("dashboard/_scratchpad.html", {
        "request": request,
        "scratchpad_content": content,
        "scratchpad_updated": row["updated_at"] if row else "",
    })


@router.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = "", conn=Depends(get_db)):
    results = {"clients": [], "policies": [], "activities": []}
    if q.strip():
        # Check for COR-{id} correspondence thread search
        cor_match = re.match(r'^COR-(\d+)$', q.strip(), re.IGNORECASE)
        # Check for INB-{id} inbox item search
        inb_match = re.match(r'^INB-(\d+)$', q.strip(), re.IGNORECASE)
        if cor_match:
            thread_id = int(cor_match.group(1))
            thread_activities = [dict(r) for r in conn.execute("""
                SELECT a.*, c.name AS client_name, p.policy_uid
                FROM activity_log a
                JOIN clients c ON a.client_id = c.id
                LEFT JOIN policies p ON a.policy_id = p.id
                WHERE a.thread_id = ?
                ORDER BY a.activity_date DESC
            """, (thread_id,)).fetchall()]
            results["activities"] = thread_activities
        elif inb_match:
            inbox_id = int(inb_match.group(1))
            item = conn.execute("""
                SELECT i.*, c.name AS client_name, a.subject AS activity_subject, a.id AS act_id
                FROM inbox i LEFT JOIN clients c ON i.client_id = c.id
                LEFT JOIN activity_log a ON i.activity_id = a.id
                WHERE i.id = ?
            """, (inbox_id,)).fetchone()
            if item:
                item = dict(item)
                # If processed with an activity, show the linked activity
                if item.get("act_id"):
                    linked = conn.execute("""
                        SELECT a.*, c.name AS client_name, p.policy_uid
                        FROM activity_log a
                        JOIN clients c ON a.client_id = c.id
                        LEFT JOIN policies p ON a.policy_id = p.id
                        WHERE a.id = ?
                    """, (item["act_id"],)).fetchall()
                    results["activities"] = [dict(r) for r in linked]
                # If has a client, show the client
                if item.get("client_id"):
                    client = conn.execute("SELECT * FROM clients WHERE id = ?", (item["client_id"],)).fetchone()
                    if client:
                        results["clients"] = [dict(client)]
        else:
            raw = full_text_search(conn, q.strip())
            results = {k: [dict(r) for r in v] for k, v in raw.items()}
    total = sum(len(v) for v in results.values())
    # Detect UID pattern for ref tree banner
    uid_pattern = bool(re.match(
        r'^(CN?\d{5,}|POL-|COR-\d+|INB-\d+|A-\d+|CN?\d+-RFI\d+|KB-|KBD-)',
        q.strip(), re.IGNORECASE
    )) if q.strip() else False
    return templates.TemplateResponse("search.html", {
        "request": request,
        "active": "",
        "q": q,
        "results": results,
        "total": total,
        "uid_detected": uid_pattern,
    })


@router.get("/api/nav/issues-dot", response_class=HTMLResponse)
async def nav_issues_dot(conn=Depends(get_db)):
    count = conn.execute(
        "SELECT COUNT(*) FROM activity_log WHERE item_kind='issue' AND merged_into_id IS NULL AND issue_status NOT IN ('Resolved','Closed')"
    ).fetchone()[0]
    if count > 0:
        return HTMLResponse('<span class="w-2 h-2 rounded-full bg-red-500 inline-block ml-1"></span>')
    return HTMLResponse('<span></span>')
