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
    should_show_review_reminder,
    get_timesheet_badge,
)
from policydb.web.app import get_db, templates

router = APIRouter()

URGENCY_ORDER = ["EXPIRED", "URGENT", "WARNING", "UPCOMING", "OK"]


def _attach_client_ids(conn, rows: list[dict]) -> list[dict]:
    """Attach client_id to each row.

    Prefers an existing client_id already on the row — the pipeline
    views (``v_renewal_pipeline`` etc.) already expose it, so we skip
    the lookup entirely in the common case.  Falls back to a single
    bulk ``(name → id)`` lookup for legacy callers that only supply
    ``client_name``.

    The lookup intentionally does **not** filter archived clients:
    pipeline views may still surface a policy whose owning client is
    archived (the view only filters ``p.archived = 0``), and dropping
    the id would turn the row's "Client" link into a broken
    ``/clients/0`` href.  Rows with no match fall back to
    ``client_id=0`` so callers can still render safely.
    """
    rows = list(rows)
    if not rows:
        return rows
    # Fast path: if every row already has a client_id, no lookup needed.
    missing = [r for r in rows if not r.get("client_id")]
    if missing:
        name_map = {
            r["name"]: r["id"]
            for r in conn.execute("SELECT id, name FROM clients").fetchall()
        }
        for d in missing:
            d["client_id"] = name_map.get(d.get("client_name") or "", 0)
    # Ensure every row at least has the key, even when nothing matched.
    for d in rows:
        d.setdefault("client_id", 0)
    return rows


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
    return templates.TemplateResponse("dashboard/_pipeline_section.html", {
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
    try:
        timesheet_badge = get_timesheet_badge(conn)
    except Exception:
        timesheet_badge = {"flags": 0, "unreviewed_emails": 0}
    note_row = conn.execute("SELECT content, updated_at FROM user_notes WHERE id=1").fetchone()

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

    show_review_reminder = should_show_review_reminder(conn, cfg.get("review_reminder_day", "monday"))

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
        "window": 90,
        "status": "",
        "scratchpad_content": scratchpad_content,
        "scratchpad_updated": scratchpad_updated,
        "recent_client_notes": recent_client_notes,
        "stale": stale,
        "escalation_alerts": escalation_alerts,
        "readiness_counts": readiness_counts,
        "suggested_uids": suggested_uids,
        "open_opportunities": open_opportunities,
        "opportunity_statuses": cfg.get("opportunity_statuses", []),
        "issues_widget": issues_widget,
        "hours_this_month": hours_this_month,
        "show_review_reminder": show_review_reminder,
        "timesheet_badge": timesheet_badge,
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
    _empty = {
        "clients": [], "policies": [], "activities": [], "issues": [],
        "contacts": [], "programs": [], "locations": [],
        "inbox": [], "kb_bookmarks": [], "kb_articles": [],
        "scratchpads": [],
        "_snippets": {}, "_query_mode": "none",
    }
    results = dict(_empty)
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
                if item.get("act_id"):
                    linked = conn.execute("""
                        SELECT a.*, c.name AS client_name, p.policy_uid
                        FROM activity_log a
                        JOIN clients c ON a.client_id = c.id
                        LEFT JOIN policies p ON a.policy_id = p.id
                        WHERE a.id = ?
                    """, (item["act_id"],)).fetchall()
                    results["activities"] = [dict(r) for r in linked]
                if item.get("client_id"):
                    client = conn.execute("SELECT * FROM clients WHERE id = ?", (item["client_id"],)).fetchone()
                    if client:
                        results["clients"] = [dict(client)]
        else:
            results = full_text_search(conn, q.strip())
    snippets = results.pop("_snippets", {})
    query_mode = results.pop("_query_mode", "none")
    total = sum(len(v) for v in results.values())
    # Detect UID pattern for ref tree banner
    uid_pattern = bool(re.match(
        r'^(CN?\d{5,}|POL-|COR-\d+|INB-\d+|A-\d+|CN?\d+-RFI\d+|PGM-)',
        q.strip(), re.IGNORECASE
    )) if q.strip() else False
    return templates.TemplateResponse("search.html", {
        "request": request,
        "active": "",
        "q": q,
        "results": results,
        "total": total,
        "uid_detected": uid_pattern,
        "snippets": snippets,
        "query_mode": query_mode,
    })


@router.get("/search/live", response_class=HTMLResponse)
def search_live(request: Request, q: str = "", conn=Depends(get_db)):
    """Return compact search dropdown partial for live search-as-you-type."""
    if not q.strip() or len(q.strip()) < 2:
        return HTMLResponse("")
    results = full_text_search(conn, q.strip())
    snippets = results.pop("_snippets", {})
    results.pop("_query_mode", None)
    # Flatten to a ranked list, max 8 items
    items = []
    # Priority order for display
    for etype in ("clients", "policies", "issues", "contacts", "programs",
                  "activities", "locations", "scratchpads", "inbox",
                  "kb_articles", "kb_bookmarks"):
        for r in results.get(etype, [])[:3]:
            items.append({"type": etype, "data": r})
            if len(items) >= 8:
                break
        if len(items) >= 8:
            break
    return templates.TemplateResponse("_search_dropdown.html", {
        "request": request,
        "items": items,
        "q": q,
        "total": sum(len(v) for v in results.values()),
    })


@router.get("/api/nav/issues-dot", response_class=HTMLResponse)
async def nav_issues_dot(conn=Depends(get_db)):
    count = conn.execute(
        "SELECT COUNT(*) FROM activity_log WHERE item_kind='issue' AND merged_into_id IS NULL AND issue_status NOT IN ('Resolved','Closed')"
    ).fetchone()[0]
    if count > 0:
        return HTMLResponse('<span class="w-2 h-2 rounded-full bg-red-500 inline-block ml-1"></span>')
    return HTMLResponse('<span></span>')
