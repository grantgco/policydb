"""Issue tracking routes — create, update status, resolve, detail view."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

import policydb.config as cfg
from policydb.db import generate_issue_uid
from policydb.utils import round_duration
from policydb.web.app import get_db, templates

router = APIRouter()


# ── Create issue ─────────────────────────────────────────────────────────────


@router.post("/issues/create", response_class=HTMLResponse)
def create_issue(
    request: Request,
    subject: str = Form(...),
    client_id: int = Form(...),
    severity: str = Form("Normal"),
    details: str = Form(""),
    policy_id: int = Form(0),
    program_id: int = Form(0),
    conn=Depends(get_db),
):
    """Create a new issue header row in activity_log."""
    today = date.today().isoformat()

    # Look up SLA from severity config
    severities = cfg.get("issue_severities", [])
    sla_days = 7
    for sev in severities:
        if sev["label"] == severity:
            sla_days = sev.get("sla_days", 7)
            break

    uid = generate_issue_uid()
    conn.execute("""
        INSERT INTO activity_log (
            activity_date, client_id, policy_id, activity_type, subject, details,
            item_kind, issue_uid, issue_status, issue_severity, issue_sla_days,
            program_id, created_at
        ) VALUES (?, ?, ?, 'Issue', ?, ?, 'issue', ?, 'Open', ?, ?, ?, CURRENT_TIMESTAMP)
    """, (
        today,
        client_id,
        policy_id or None,
        subject,
        details or "",
        uid,
        severity,
        sla_days,
        program_id or None,
    ))
    conn.commit()

    # Return refreshed issues tab
    from policydb.web.routes.action_center import _issues_ctx
    ctx = _issues_ctx(conn)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_issues.html", ctx)


# ── Update issue status ──────────────────────────────────────────────────────


@router.post("/issues/{issue_id}/status", response_class=HTMLResponse)
def update_issue_status(
    issue_id: int,
    request: Request,
    status: str = Form(...),
    conn=Depends(get_db),
):
    """Quick-update issue lifecycle status."""
    conn.execute(
        "UPDATE activity_log SET issue_status = ? WHERE id = ? AND item_kind = 'issue'",
        (status, issue_id),
    )
    conn.commit()

    # Return refreshed issues tab
    from policydb.web.routes.action_center import _issues_ctx
    ctx = _issues_ctx(conn)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_issues.html", ctx)


# ── Update issue severity ────────────────────────────────────────────────────


@router.post("/issues/{issue_id}/severity", response_class=HTMLResponse)
def update_issue_severity(
    issue_id: int,
    request: Request,
    severity: str = Form(...),
    conn=Depends(get_db),
):
    """Quick-update issue severity."""
    severities = cfg.get("issue_severities", [])
    sla_days = 7
    for sev in severities:
        if sev["label"] == severity:
            sla_days = sev.get("sla_days", 7)
            break

    conn.execute(
        "UPDATE activity_log SET issue_severity = ?, issue_sla_days = ? "
        "WHERE id = ? AND item_kind = 'issue'",
        (severity, sla_days, issue_id),
    )
    conn.commit()

    from policydb.web.routes.action_center import _issues_ctx
    ctx = _issues_ctx(conn)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_issues.html", ctx)


# ── Resolve issue ────────────────────────────────────────────────────────────


@router.post("/issues/{issue_id}/resolve", response_class=HTMLResponse)
def resolve_issue(
    issue_id: int,
    request: Request,
    resolution_type: str = Form(...),
    resolution_notes: str = Form(""),
    root_cause_category: str = Form(""),
    conn=Depends(get_db),
):
    """Resolve an issue with full resolution form."""
    today = date.today().isoformat()
    conn.execute("""
        UPDATE activity_log
        SET issue_status = 'Resolved',
            resolution_type = ?,
            resolution_notes = ?,
            root_cause_category = ?,
            resolved_date = ?
        WHERE id = ? AND item_kind = 'issue'
    """, (resolution_type, resolution_notes, root_cause_category, today, issue_id))
    conn.commit()

    from policydb.web.routes.action_center import _issues_ctx
    ctx = _issues_ctx(conn)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_issues.html", ctx)


# ── Issue detail page ────────────────────────────────────────────────────────


@router.get("/issues/{issue_uid}", response_class=HTMLResponse)
def issue_detail(
    issue_uid: str,
    request: Request,
    conn=Depends(get_db),
):
    """Full issue detail page with activity timeline."""
    issue = conn.execute("""
        SELECT a.*, c.name AS client_name,
               p.policy_uid, p.policy_type, p.carrier, p.expiration_date,
               CASE WHEN a.resolved_date IS NOT NULL
                    THEN julianday(a.resolved_date) - julianday(a.activity_date)
                    ELSE julianday(date('now')) - julianday(a.activity_date)
               END AS days_open,
               CASE WHEN a.resolved_date IS NOT NULL
                    THEN julianday(a.resolved_date) - julianday(a.activity_date)
                    ELSE NULL
               END AS time_to_resolve
        FROM activity_log a
        LEFT JOIN clients c ON c.id = a.client_id
        LEFT JOIN policies p ON p.id = a.policy_id
        WHERE a.issue_uid = ? AND a.item_kind = 'issue'
    """, (issue_uid,)).fetchone()

    if not issue:
        return RedirectResponse("/action-center?tab=issues", status_code=303)

    issue = dict(issue)
    issue_id = issue["id"]

    # Get linked activities (threaded into this issue)
    activities = [dict(r) for r in conn.execute("""
        SELECT a.*, c.name AS contact_name
        FROM activity_log a
        LEFT JOIN contacts c ON c.id = a.contact_id
        WHERE a.issue_id = ?
        ORDER BY a.activity_date DESC, a.created_at DESC
    """, (issue_id,)).fetchall()]

    # Compute total hours across all linked activities
    total_hours = sum(a.get("duration_hours") or 0 for a in activities)

    # SLA info
    severities = cfg.get("issue_severities", [])
    sla_map = {s["label"]: s.get("sla_days", 7) for s in severities}
    sla = issue.get("issue_sla_days") or sla_map.get(issue.get("issue_severity", "Normal"), 7)
    issue["sla_days"] = sla
    issue["over_sla"] = (issue.get("days_open") or 0) > sla

    ctx = {
        "request": request,
        "active": "action-center",
        "issue": issue,
        "activities": activities,
        "total_hours": round(total_hours, 1),
        "issue_lifecycle_states": cfg.get("issue_lifecycle_states", []),
        "issue_severities": cfg.get("issue_severities", []),
        "issue_resolution_types": cfg.get("issue_resolution_types", []),
        "issue_root_cause_categories": cfg.get("issue_root_cause_categories", []),
        "activity_types": cfg.get("activity_types", []),
        "follow_up_dispositions": cfg.get("follow_up_dispositions", []),
    }
    return templates.TemplateResponse("issues/detail.html", ctx)


# ── Log activity against issue ───────────────────────────────────────────────


@router.post("/issues/{issue_id}/log", response_class=HTMLResponse)
def log_issue_activity(
    issue_id: int,
    request: Request,
    activity_type: str = Form("Call"),
    subject: str = Form(""),
    details: str = Form(""),
    follow_up_date: str = Form(""),
    disposition: str = Form(""),
    duration_hours: str = Form(""),
    conn=Depends(get_db),
):
    """Log an activity linked to an issue."""
    today = date.today().isoformat()

    # Get issue's client_id and policy_id for inheritance
    issue = conn.execute(
        "SELECT client_id, policy_id, program_id FROM activity_log WHERE id = ?",
        (issue_id,),
    ).fetchone()
    if not issue:
        return RedirectResponse("/action-center?tab=issues", status_code=303)

    # Resolve disposition → accountability + default_days
    fu_date = follow_up_date or None
    if disposition and not fu_date:
        disps = cfg.get("follow_up_dispositions", [])
        for d in disps:
            if d["label"] == disposition and d.get("default_days"):
                from datetime import timedelta
                fu_date = (date.today() + timedelta(days=d["default_days"])).isoformat()
                break

    conn.execute("""
        INSERT INTO activity_log (
            activity_date, client_id, policy_id, activity_type, subject, details,
            follow_up_date, disposition, issue_id, item_kind, program_id,
            duration_hours, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'followup', ?, ?, CURRENT_TIMESTAMP)
    """, (
        today,
        issue["client_id"],
        issue["policy_id"],
        activity_type,
        subject,
        details or "",
        fu_date,
        disposition or None,
        issue_id,
        issue["program_id"],
        round_duration(duration_hours),
    ))
    conn.commit()

    # Look up the issue_uid for redirect
    row = conn.execute("SELECT issue_uid FROM activity_log WHERE id=?", (issue_id,)).fetchone()
    uid = row["issue_uid"] if row else issue_id
    return RedirectResponse(f"/issues/{uid}", status_code=303)


# ── Convert follow-up to issue ───────────────────────────────────────────────


@router.post("/issues/convert/{activity_id}", response_class=HTMLResponse)
def convert_followup_to_issue(
    activity_id: int,
    request: Request,
    severity: str = Form("Normal"),
    conn=Depends(get_db),
):
    """Convert an existing follow-up activity into an issue."""
    activity = conn.execute(
        "SELECT * FROM activity_log WHERE id = ?", (activity_id,)
    ).fetchone()
    if not activity:
        return RedirectResponse("/action-center?tab=issues", status_code=303)

    activity = dict(activity)
    today = date.today().isoformat()

    severities = cfg.get("issue_severities", [])
    sla_days = 7
    for sev in severities:
        if sev["label"] == severity:
            sla_days = sev.get("sla_days", 7)
            break

    # Create issue header
    uid = generate_issue_uid()
    cur = conn.execute("""
        INSERT INTO activity_log (
            activity_date, client_id, policy_id, activity_type, subject, details,
            item_kind, issue_uid, issue_status, issue_severity, issue_sla_days,
            program_id, created_at
        ) VALUES (?, ?, ?, 'Issue', ?, ?, 'issue', ?, 'Open', ?, ?, ?, CURRENT_TIMESTAMP)
    """, (
        today,
        activity["client_id"],
        activity["policy_id"],
        activity.get("subject") or "Converted from follow-up",
        activity.get("details") or "",
        uid,
        severity,
        sla_days,
        activity.get("program_id"),
    ))
    issue_id = cur.lastrowid

    # Link original activity to the issue
    conn.execute(
        "UPDATE activity_log SET issue_id = ? WHERE id = ?",
        (issue_id, activity_id),
    )
    conn.commit()

    return RedirectResponse(f"/issues/{uid}", status_code=303)
