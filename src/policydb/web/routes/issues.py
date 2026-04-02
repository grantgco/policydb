"""Issue tracking routes — create, update status, resolve, detail view."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from rapidfuzz import fuzz

import policydb.config as cfg
from policydb.db import generate_issue_uid
from policydb.queries import auto_close_followups
from policydb.utils import round_duration
from policydb.web.app import get_db, templates

router = APIRouter()


def _get_issue_checklist(conn, issue_id: int) -> list[dict]:
    """Return checklist items for an issue, ordered by sort_order then id."""
    return [dict(r) for r in conn.execute(
        "SELECT * FROM issue_checklist WHERE issue_id=? ORDER BY sort_order, id",
        (issue_id,),
    ).fetchall()]


class IssueDetailsUpdate(BaseModel):
    details: str = ""


class IssueSubjectUpdate(BaseModel):
    subject: str


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
    source_activity_id: int = Form(0),
    source_activity_ids: str = Form(""),
    due_date: str = Form(""),
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
    cur = conn.execute("""
        INSERT INTO activity_log (
            activity_date, client_id, policy_id, activity_type, subject, details,
            item_kind, issue_uid, issue_status, issue_severity, issue_sla_days,
            program_id, due_date, created_at
        ) VALUES (?, ?, ?, 'Issue', ?, ?, 'issue', ?, 'Open', ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
        due_date or None,
    ))
    new_issue_id = cur.lastrowid

    # Link source activities to the new issue
    if source_activity_id:
        conn.execute(
            "UPDATE activity_log SET issue_id = ? WHERE id = ?",
            (new_issue_id, source_activity_id),
        )
    elif source_activity_ids:
        try:
            ids = [int(x.strip()) for x in source_activity_ids.split(",") if x.strip()]
            if ids:
                placeholders = ",".join("?" * len(ids))
                conn.execute(
                    f"UPDATE activity_log SET issue_id = ? WHERE id IN ({placeholders})",
                    [new_issue_id] + ids,
                )
        except ValueError:
            pass

    conn.commit()

    # Redirect to the new issue's detail page for editing
    return RedirectResponse(f"/issues/{uid}", status_code=303)


# ── Update issue status ──────────────────────────────────────────────────────


@router.post("/issues/{issue_id}/status", response_class=HTMLResponse)
def update_issue_status(
    issue_id: int,
    request: Request,
    status: str = Form(...),
    redirect: str = Query(""),
    conn=Depends(get_db),
):
    """Quick-update issue lifecycle status."""
    conn.execute(
        "UPDATE activity_log SET issue_status = ? WHERE id = ? AND item_kind = 'issue'",
        (status, issue_id),
    )

    closed = 0
    if status in ("Resolved", "Closed"):
        closed = auto_close_followups(
            conn, issue_id=issue_id, reason="issue_resolved", closed_by="issue_status_change",
        )
    conn.commit()

    if redirect:
        return RedirectResponse(redirect, status_code=303)

    # Return refreshed issues tab
    from policydb.web.routes.action_center import _issues_ctx
    ctx = _issues_ctx(conn)
    ctx["request"] = request
    resp = templates.TemplateResponse("action_center/_issues.html", ctx)
    if closed:
        resp.headers["HX-Trigger"] = f'{{"showToast": "{closed} follow-up(s) auto-closed: issue {status.lower()}"}}'
    return resp


# ── Update issue severity ────────────────────────────────────────────────────


@router.post("/issues/{issue_id}/severity", response_class=HTMLResponse)
def update_issue_severity(
    issue_id: int,
    request: Request,
    severity: str = Form(...),
    redirect: str = Query(""),
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

    if redirect:
        return RedirectResponse(redirect, status_code=303)

    from policydb.web.routes.action_center import _issues_ctx
    ctx = _issues_ctx(conn)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_issues.html", ctx)


# ── Update issue due date ───────────────────────────────────────────────────


@router.post("/issues/{issue_id}/due-date", response_class=HTMLResponse)
def update_issue_due_date(
    issue_id: int,
    request: Request,
    due_date: str = Form(""),
    redirect: str = Query(""),
    conn=Depends(get_db),
):
    """Quick-update issue due date."""
    conn.execute(
        "UPDATE activity_log SET due_date = ? WHERE id = ? AND item_kind = 'issue'",
        (due_date or None, issue_id),
    )
    conn.commit()

    if redirect:
        return RedirectResponse(redirect, status_code=303)

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

    closed = auto_close_followups(
        conn, issue_id=issue_id, reason="issue_resolved", closed_by="issue_resolve",
    )
    conn.commit()

    # Redirect to action center issues tab after resolve
    toast = "Issue resolved"
    if closed:
        toast += f" — {closed} follow-up(s) auto-closed"
    resp = HTMLResponse("")
    resp.headers["HX-Redirect"] = "/action-center?tab=issues"
    resp.headers["HX-Trigger"] = '{"showToast": "' + toast + '"}'
    return resp


# ── Update issue details ─────────────────────────────────────────────────────


@router.patch("/issues/{issue_id}/details")
def update_issue_details(
    issue_id: int,
    body: IssueDetailsUpdate,
    conn=Depends(get_db),
):
    """Update issue details/description field via PATCH."""
    conn.execute(
        "UPDATE activity_log SET details = ? WHERE id = ? AND item_kind = 'issue'",
        (body.details, issue_id),
    )
    conn.commit()
    return {"ok": True}


# ── Edit slideover ────────────────────────────────────────────────────────────


@router.get("/issues/{issue_id}/edit-slideover", response_class=HTMLResponse)
def issue_edit_slideover(issue_id: int, request: Request, conn=Depends(get_db)):
    """Return the edit slideover partial for an issue."""
    row = conn.execute(
        """SELECT a.id, a.issue_uid, a.subject, a.details, a.due_date,
                  a.issue_severity, a.issue_status,
                  c.name AS client_name
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           WHERE a.id = ? AND a.item_kind = 'issue'""",
        (issue_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("<p class='p-4 text-sm text-gray-400'>Not found.</p>", status_code=404)
    return templates.TemplateResponse("action_center/_edit_issue_slideover.html", {
        "request": request,
        "iss": dict(row),
        "lifecycle_states": cfg.get("issue_lifecycle_states", []),
        "severities": cfg.get("issue_severities", []),
    })


@router.patch("/issues/{issue_id}/field")
def patch_issue_field(issue_id: int, body: dict = None, conn=Depends(get_db)):
    """Update a single field on an issue (slideover inline edit)."""
    if not body:
        return JSONResponse({"ok": False, "error": "No body"}, status_code=400)
    field = body.get("field", "")
    value = body.get("value", "")

    allowed = {"due_date", "issue_severity", "issue_status", "subject", "details"}
    if field not in allowed:
        return JSONResponse({"ok": False, "error": f"Field '{field}' not editable"}, status_code=400)

    if field == "subject" and not (value or "").strip():
        return JSONResponse({"ok": False, "error": "Subject cannot be empty"}, status_code=400)

    # Update SLA when severity changes
    if field == "issue_severity":
        severities = cfg.get("issue_severities", [])
        sla_days = 7
        for sev in severities:
            if sev["label"] == value:
                sla_days = sev.get("sla_days", 7)
                break
        conn.execute(
            "UPDATE activity_log SET issue_severity = ?, issue_sla_days = ? WHERE id = ? AND item_kind = 'issue'",
            (value, sla_days, issue_id),
        )
    elif field == "issue_status":
        conn.execute(
            "UPDATE activity_log SET issue_status = ? WHERE id = ? AND item_kind = 'issue'",
            (value, issue_id),
        )
        if value in ("Resolved", "Closed"):
            auto_close_followups(conn, issue_id=issue_id, reason="issue_resolved", closed_by="issue_status_change")
    else:
        conn.execute(
            f"UPDATE activity_log SET {field} = ? WHERE id = ? AND item_kind = 'issue'",
            (value or None, issue_id),
        )

    conn.commit()
    return {"ok": True, "formatted": value}


@router.patch("/issues/{issue_id}/subject")
def update_issue_subject(
    issue_id: int,
    body: IssueSubjectUpdate,
    conn=Depends(get_db),
):
    """Update issue subject/title via PATCH."""
    subject = body.subject.strip()
    if not subject:
        return {"ok": False, "error": "Subject cannot be empty"}
    conn.execute(
        "UPDATE activity_log SET subject = ? WHERE id = ? AND item_kind = 'issue'",
        (subject, issue_id),
    )
    conn.commit()
    return {"ok": True, "formatted": subject}


# ── Open issues for a client (widget partial) ────────────────────────────────


_SEVERITY_ORDER = {"Critical": 0, "High": 1, "Normal": 2, "Low": 3}


@router.get("/issues/for-client/{client_id}", response_class=HTMLResponse)
def issues_for_client(
    client_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """Return open issues for a client — used by the Quick Log issue widget."""
    rows = conn.execute("""
        SELECT id, issue_uid, subject, issue_severity, issue_sla_days,
               CAST(julianday('now') - julianday(activity_date) AS INTEGER) AS days_open
        FROM activity_log
        WHERE item_kind = 'issue'
          AND issue_id IS NULL
          AND client_id = ?
          AND (issue_status IS NULL OR issue_status NOT IN ('Resolved', 'Closed'))
    """, (client_id,)).fetchall()

    issues = sorted(
        [dict(r) for r in rows],
        key=lambda r: (
            _SEVERITY_ORDER.get(r.get("issue_severity") or "Normal", 2),
            r.get("days_open") or 0,
        ),
    )
    return templates.TemplateResponse(
        "issues/_issue_widget.html",
        {"request": request, "issues": issues},
    )


@router.get("/issues/for-policy/{policy_id}", response_class=HTMLResponse)
def issues_for_policy(
    policy_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """Return open issues for a specific policy — used by policy page issue widgets."""
    rows = conn.execute("""
        SELECT id, issue_uid, subject, issue_severity, issue_sla_days,
               CAST(julianday('now') - julianday(activity_date) AS INTEGER) AS days_open
        FROM activity_log
        WHERE item_kind = 'issue'
          AND issue_id IS NULL
          AND policy_id = ?
          AND (issue_status IS NULL OR issue_status NOT IN ('Resolved', 'Closed'))
    """, (policy_id,)).fetchall()

    issues = sorted(
        [dict(r) for r in rows],
        key=lambda r: (
            _SEVERITY_ORDER.get(r.get("issue_severity") or "Normal", 2),
            r.get("days_open") or 0,
        ),
    )
    return templates.TemplateResponse(
        "issues/_issue_widget.html",
        {"request": request, "issues": issues},
    )


# ── Linkable activities for an issue ─────────────────────────────────────────


@router.get("/issues/{issue_id}/linkable-activities", response_class=HTMLResponse)
def linkable_activities(
    issue_id: int,
    request: Request,
    q: str = Query(""),
    activity_type: str = Query(""),
    days: int = Query(30),
    conn=Depends(get_db),
):
    """Return unlinked activities that can be linked to an issue."""
    issue = conn.execute(
        "SELECT client_id FROM activity_log WHERE id = ? AND item_kind = 'issue'",
        (issue_id,),
    ).fetchone()
    if not issue:
        return HTMLResponse("<p class='text-sm text-gray-500 p-4'>Issue not found.</p>")

    client_id = issue["client_id"]
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    params: list = [client_id, cutoff]
    extra = ""
    if q:
        extra += " AND a.subject LIKE ?"
        params.append(f"%{q}%")
    if activity_type:
        extra += " AND a.activity_type = ?"
        params.append(activity_type)

    rows = conn.execute(f"""
        SELECT a.id, a.activity_date, a.activity_type, a.subject, a.details,
               a.duration_hours, p.policy_uid, p.policy_type
        FROM activity_log a
        LEFT JOIN policies p ON p.id = a.policy_id
        WHERE a.client_id = ?
          AND a.activity_date >= ?
          AND a.issue_id IS NULL
          AND (a.item_kind = 'followup' OR a.item_kind IS NULL)
          {extra}
        ORDER BY a.activity_date DESC
    """, params).fetchall()

    activities = [dict(r) for r in rows]
    return templates.TemplateResponse(
        "issues/_linkable_list.html",
        {
            "request": request,
            "activities": activities,
            "activity_types": cfg.get("activity_types", []),
        },
    )


# ── Bulk-link activities to an issue ─────────────────────────────────────────


@router.post("/issues/{issue_id}/link-activities", response_class=HTMLResponse)
def link_activities(
    issue_id: int,
    request: Request,
    activity_ids: str = Form(default=""),
    conn=Depends(get_db),
):
    """Bulk-link selected activities to an issue."""
    # Parse comma-separated IDs from the hidden input
    parsed_ids = []
    for v in activity_ids.split(","):
        v = v.strip()
        if v.isdigit():
            parsed_ids.append(int(v))
    if parsed_ids:
        placeholders = ",".join("?" * len(parsed_ids))
        conn.execute(
            f"UPDATE activity_log SET issue_id = ? WHERE id IN ({placeholders})",
            [issue_id] + parsed_ids,
        )
        conn.commit()

    # Redirect to the issue detail page
    row = conn.execute(
        "SELECT issue_uid FROM activity_log WHERE id = ?", (issue_id,)
    ).fetchone()
    uid = row["issue_uid"] if row else str(issue_id)
    return RedirectResponse(f"/issues/{uid}", status_code=303)


# ── Unlink activity from issue ────────────────────────────────────────────────

@router.delete("/issues/{issue_id}/unlink-activity/{activity_id}")
async def unlink_activity_from_issue(issue_id: int, activity_id: int, conn=Depends(get_db)):
    conn.execute(
        "UPDATE activity_log SET issue_id = NULL WHERE id = ? AND issue_id = ?",
        (activity_id, issue_id),
    )
    conn.commit()
    return HTMLResponse("")


# ── Issue policy linking ─────────────────────────────────────────────────────


@router.get("/issues/{issue_id}/policies/search")
def search_issue_policies(issue_id: int, q: str = Query(""), conn=Depends(get_db)):
    """Return client policies available to link to this issue (includes opportunities)."""
    issue = conn.execute(
        "SELECT client_id FROM activity_log WHERE id = ?", (issue_id,)
    ).fetchone()
    if not issue:
        return JSONResponse([])

    # Already-linked policy IDs (direct FK + junction)
    linked_ids = set()
    row = conn.execute(
        "SELECT policy_id FROM activity_log WHERE id = ?", (issue_id,)
    ).fetchone()
    if row and row["policy_id"]:
        linked_ids.add(row["policy_id"])
    for r in conn.execute(
        "SELECT policy_id FROM issue_policies WHERE issue_id = ?", (issue_id,)
    ).fetchall():
        linked_ids.add(r["policy_id"])

    term = f"%{q}%"
    rows = conn.execute("""
        SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.is_opportunity,
               pr.name AS location_name
        FROM policies p
        LEFT JOIN projects pr ON pr.id = p.project_id
        WHERE p.client_id = ? AND p.archived = 0
          AND (p.policy_uid LIKE ? OR p.policy_type LIKE ? OR p.carrier LIKE ?
               OR pr.name LIKE ?)
        ORDER BY p.policy_uid
        LIMIT 20
    """, (issue["client_id"], term, term, term, term)).fetchall()

    results = []
    for r in rows:
        if r["id"] not in linked_ids:
            label = r["policy_uid"]
            if r["policy_type"]:
                label += f" — {r['policy_type']}"
            if r["carrier"]:
                label += f" ({r['carrier']})"
            if r["location_name"]:
                label += f" @ {r['location_name']}"
            if r["is_opportunity"]:
                label += " [OPP]"
            results.append({"id": r["id"], "label": label, "policy_uid": r["policy_uid"]})
    return JSONResponse(results)


@router.post("/issues/{issue_id}/policies/add")
def add_issue_policy(issue_id: int, policy_id: int = Form(...), conn=Depends(get_db)):
    """Link a policy to an issue via junction table."""
    conn.execute(
        "INSERT OR IGNORE INTO issue_policies (issue_id, policy_id) VALUES (?, ?)",
        (issue_id, policy_id),
    )
    conn.commit()
    return _render_linked_policies_panel(issue_id, conn)


@router.post("/issues/{issue_id}/policies/{policy_id}/remove")
def remove_issue_policy(issue_id: int, policy_id: int, conn=Depends(get_db)):
    """Unlink a policy from an issue (junction table only, not the primary FK)."""
    conn.execute(
        "DELETE FROM issue_policies WHERE issue_id = ? AND policy_id = ?",
        (issue_id, policy_id),
    )
    conn.commit()
    return _render_linked_policies_panel(issue_id, conn)


def _render_linked_policies_panel(issue_id: int, conn) -> HTMLResponse:
    """Return the updated linked-policies panel partial."""
    issue = conn.execute(
        "SELECT policy_id, program_id FROM activity_log WHERE id = ?", (issue_id,)
    ).fetchone()
    linked = _get_linked_policies(conn, issue_id, issue)
    html_parts = []
    for p in linked:
        is_junction = p.get("_junction")
        remove_btn = ""
        if is_junction:
            remove_btn = (
                f'<button hx-post="/issues/{issue_id}/policies/{p["id"]}/remove" '
                f'hx-target="#linked-policies-panel" hx-swap="innerHTML" '
                f'class="text-gray-300 hover:text-red-500 text-xs ml-1 shrink-0 no-print" '
                f'title="Remove">&times;</button>'
            )
        opp_badge = ""
        if p.get("is_opportunity"):
            opp_badge = '<span class="text-[9px] px-1 py-0.5 rounded bg-purple-100 text-purple-600 shrink-0">OPP</span>'
        status_badge = ""
        if p.get("renewal_status"):
            cls = "bg-gray-100 text-gray-600"
            if p["renewal_status"] == "Bound":
                cls = "bg-green-100 text-green-700"
            elif p["renewal_status"] == "In Progress":
                cls = "bg-blue-100 text-blue-700"
            status_badge = f'<span class="text-[10px] px-1.5 py-0.5 rounded-full shrink-0 {cls}">{p["renewal_status"]}</span>'
        premium_str = ""
        if p.get("premium"):
            premium_str = f'<span class="text-xs text-gray-500 tabular-nums shrink-0 ml-auto">${p["premium"]:,.0f}</span>'
        exp_str = ""
        if p.get("expiration_date"):
            exp_str = f'<span class="text-[10px] text-gray-400 shrink-0">exp {p["expiration_date"]}</span>'
        carrier_str = ""
        if p.get("carrier"):
            carrier_str = f'<span class="text-xs text-gray-500 truncate">{p["carrier"]}</span>'
        html_parts.append(
            f'<div class="flex items-center gap-3 px-4 py-2.5 hover:bg-gray-50 text-sm">'
            f'<a href="/policies/{p["policy_uid"]}/edit" class="font-mono text-xs text-marsh hover:underline shrink-0">{p["policy_uid"]}</a>'
            f'<span class="text-gray-800 truncate">{p.get("policy_type") or ""}</span>'
            f'{opp_badge}{carrier_str}'
            f'{premium_str}{exp_str}{status_badge}{remove_btn}'
            f'</div>'
        )
    count = len(linked)
    header = (
        f'<div class="px-4 py-2.5 bg-gray-50 border-b border-gray-200 flex items-center justify-between">'
        f'<span class="text-[10px] font-semibold text-gray-500 uppercase tracking-wide">'
        f'Linked Policies <span class="text-gray-400 normal-case">({count})</span></span></div>'
    )
    body = f'<div class="divide-y divide-gray-100">{"".join(html_parts)}</div>' if html_parts else ""
    return HTMLResponse(header + body)


def _get_linked_policies(conn, issue_id: int, issue) -> list[dict]:
    """Gather all linked policies: direct FK + program + junction table."""
    linked = []
    seen_ids = set()

    # Direct FK
    if issue and issue["policy_id"]:
        rows = conn.execute("""
            SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.premium,
                   p.expiration_date, p.renewal_status, p.is_opportunity,
                   pr.name AS location_name
            FROM policies p
            LEFT JOIN projects pr ON pr.id = p.project_id
            WHERE p.id = ? AND p.archived = 0
        """, (issue["policy_id"],)).fetchall()
        for r in rows:
            d = dict(r)
            seen_ids.add(d["id"])
            linked.append(d)

    # Program siblings
    if issue and issue["program_id"]:
        rows = conn.execute("""
            SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.premium,
                   p.expiration_date, p.renewal_status, p.is_opportunity,
                   pr.name AS location_name
            FROM policies p
            LEFT JOIN projects pr ON pr.id = p.project_id
            WHERE p.program_id = ? AND p.archived = 0
            ORDER BY p.policy_type
        """, (issue["program_id"],)).fetchall()
        for r in rows:
            d = dict(r)
            if d["id"] not in seen_ids:
                seen_ids.add(d["id"])
                linked.append(d)

    # Junction table
    rows = conn.execute("""
        SELECT p.id, p.policy_uid, p.policy_type, p.carrier, p.premium,
               p.expiration_date, p.renewal_status, p.is_opportunity,
               pr.name AS location_name
        FROM issue_policies ip
        JOIN policies p ON p.id = ip.policy_id
        LEFT JOIN projects pr ON pr.id = p.project_id
        WHERE ip.issue_id = ? AND p.archived = 0
        ORDER BY p.policy_uid
    """, (issue_id,)).fetchall()
    for r in rows:
        d = dict(r)
        if d["id"] not in seen_ids:
            d["_junction"] = True
            seen_ids.add(d["id"])
            linked.append(d)

    return linked


# ── Issue detail page ────────────────────────────────────────────────────────


@router.get("/issues/{issue_uid}", response_class=HTMLResponse)
def issue_detail(
    issue_uid: str,
    request: Request,
    merged_from: str = Query(""),
    conn=Depends(get_db),
):
    """Full issue detail page with activity timeline."""
    issue = conn.execute("""
        SELECT a.*, c.name AS client_name,
               p.policy_uid, p.policy_type, p.carrier, p.expiration_date,
               p.is_opportunity, p.opportunity_status, p.target_effective_date,
               p.premium AS policy_premium,
               pr.name AS location_name,
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
        LEFT JOIN projects pr ON pr.id = p.project_id
        WHERE a.issue_uid = ? AND a.item_kind = 'issue'
    """, (issue_uid,)).fetchone()

    if not issue:
        return RedirectResponse("/action-center?tab=issues", status_code=303)

    issue = dict(issue)
    issue_id = issue["id"]

    # Resolve the final merge target for display (follow chain)
    merged_into_issue = None
    if issue.get("merged_into_id"):
        cur_id = issue["merged_into_id"]
        for _ in range(10):  # guard against cycles
            row = conn.execute(
                "SELECT id, issue_uid, subject, merged_into_id FROM activity_log WHERE id = ?",
                (cur_id,),
            ).fetchone()
            if not row:
                break
            if row["merged_into_id"]:
                cur_id = row["merged_into_id"]
            else:
                merged_into_issue = dict(row)
                break

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

    # Query issues that were merged into this one
    merged_from_issues = [dict(r) for r in conn.execute("""
        SELECT id, issue_uid, subject, resolved_date
        FROM activity_log
        WHERE merged_into_id = ? AND item_kind = 'issue'
        ORDER BY resolved_date DESC
    """, (issue_id,)).fetchall()]

    # For renewal issues, include timeline milestone data
    timeline_milestones = []
    if issue.get("is_renewal_issue"):
        policy_uid = issue.get("policy_uid")
        if policy_uid:
            timeline_milestones = [dict(r) for r in conn.execute("""
                SELECT milestone_name, ideal_date, projected_date, completed_date,
                       health, accountability, waiting_on
                FROM policy_timeline
                WHERE policy_uid = ?
                ORDER BY ideal_date
            """, (policy_uid,)).fetchall()]
        elif issue.get("program_id"):
            # Program-level: aggregate child policy milestones
            timeline_milestones = [dict(r) for r in conn.execute("""
                SELECT pt.milestone_name, pt.ideal_date, pt.projected_date,
                       pt.completed_date, pt.health, pt.accountability, pt.waiting_on
                FROM policy_timeline pt
                JOIN policies p ON p.policy_uid = pt.policy_uid
                WHERE p.program_id = ?
                ORDER BY pt.ideal_date
            """, (issue["program_id"],)).fetchall()]

    # Linked policies: direct FK + program siblings + junction table
    linked_policies = _get_linked_policies(conn, issue_id, issue)

    ctx = {
        "request": request,
        "active": "action-center",
        "issue": issue,
        "activities": activities,
        "total_hours": round(total_hours, 1),
        "merged_from_issues": merged_from_issues,
        "merged_from_flash": merged_from or "",
        "linked_policies": linked_policies,
        "issue_lifecycle_states": cfg.get("issue_lifecycle_states", []),
        "issue_severities": cfg.get("issue_severities", []),
        "issue_resolution_types": cfg.get("issue_resolution_types", []),
        "issue_root_cause_categories": cfg.get("issue_root_cause_categories", []),
        "activity_types": cfg.get("activity_types", []),
        "follow_up_dispositions": cfg.get("follow_up_dispositions", []),
        "timeline_milestones": timeline_milestones,
        "merged_into_issue": merged_into_issue,
        "today": date.today().isoformat(),
        "checklist_items": _get_issue_checklist(conn, issue_id),
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


# ── Issue checklist ─────────────────────────────────────────────────────────


@router.get("/issues/{issue_id}/checklist", response_class=HTMLResponse)
def issue_checklist_get(issue_id: int, request: Request, conn=Depends(get_db)):
    """Return the checklist card partial for HTMX refresh."""
    items = _get_issue_checklist(conn, issue_id)
    return templates.TemplateResponse("issues/_issue_checklist.html", {
        "request": request, "issue": {"id": issue_id}, "checklist_items": items,
    })


@router.post("/issues/{issue_id}/checklist", response_class=HTMLResponse)
def issue_checklist_add(
    issue_id: int, request: Request,
    label: str = Form(""), conn=Depends(get_db),
):
    """Add a new checklist item to an issue."""
    label = label.strip()
    if label:
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) FROM issue_checklist WHERE issue_id=?",
            (issue_id,),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO issue_checklist (issue_id, label, sort_order) VALUES (?, ?, ?)",
            (issue_id, label, max_order + 1),
        )
        conn.commit()
    items = _get_issue_checklist(conn, issue_id)
    return templates.TemplateResponse("issues/_issue_checklist.html", {
        "request": request, "issue": {"id": issue_id}, "checklist_items": items,
    })


@router.patch("/issues/{issue_id}/checklist/{item_id}")
async def issue_checklist_update(
    issue_id: int, item_id: int, request: Request, conn=Depends(get_db),
):
    """Toggle completed or update label on a checklist item."""
    body = await request.json()

    row = conn.execute("SELECT * FROM issue_checklist WHERE id=? AND issue_id=?", (item_id, issue_id)).fetchone()
    if not row:
        return JSONResponse({"ok": False, "error": "Not found"}, status_code=404)

    if "completed" in body:
        completed = 1 if body["completed"] else 0
        completed_at = datetime.now().isoformat() if completed else None
        conn.execute(
            "UPDATE issue_checklist SET completed=?, completed_at=? WHERE id=?",
            (completed, completed_at, item_id),
        )
    if "label" in body:
        label = body["label"].strip()
        if label:
            conn.execute("UPDATE issue_checklist SET label=? WHERE id=?", (label, item_id))
    conn.commit()
    return JSONResponse({"ok": True})


@router.delete("/issues/{issue_id}/checklist/{item_id}", response_class=HTMLResponse)
def issue_checklist_delete(
    issue_id: int, item_id: int, request: Request, conn=Depends(get_db),
):
    """Remove a checklist item."""
    conn.execute("DELETE FROM issue_checklist WHERE id=? AND issue_id=?", (item_id, issue_id))
    conn.commit()
    items = _get_issue_checklist(conn, issue_id)
    return templates.TemplateResponse("issues/_issue_checklist.html", {
        "request": request, "issue": {"id": issue_id}, "checklist_items": items,
    })


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


# ── Delete issue ────────────────────────────────────────────────────────────


@router.delete("/issues/{issue_id}", response_class=HTMLResponse)
def delete_issue(
    issue_id: int,
    request: Request,
    redirect: str = Query(""),
    conn=Depends(get_db),
):
    """Hard-delete an issue. Unlinks child activities first."""
    issue = conn.execute(
        "SELECT id FROM activity_log WHERE id = ? AND item_kind = 'issue'",
        (issue_id,),
    ).fetchone()
    if not issue:
        return HTMLResponse("<p>Issue not found.</p>", status_code=404)

    # Unlink child activities
    conn.execute("UPDATE activity_log SET issue_id = NULL WHERE issue_id = ?", (issue_id,))
    # Delete the issue row
    conn.execute("DELETE FROM activity_log WHERE id = ? AND item_kind = 'issue'", (issue_id,))
    conn.commit()

    # If called from detail page (hx-target=body), redirect to issues list
    if request.headers.get("hx-target") == "body":
        return RedirectResponse("/action-center?tab=issues", status_code=303)

    # Return refreshed issues tab (called from list row)
    from policydb.web.routes.action_center import _issues_ctx
    ctx = _issues_ctx(conn)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_issues.html", ctx)


# ── Merge issues ────────────────────────────────────────────────────────────


@router.post("/issues/{target_id}/merge", response_class=HTMLResponse)
def merge_issues(
    target_id: int,
    request: Request,
    source_ids: str = Form(""),
    conn=Depends(get_db),
):
    """Merge source issues into a target issue. Relinks activities, closes sources."""
    target = conn.execute(
        "SELECT id, issue_uid FROM activity_log WHERE id = ? AND item_kind = 'issue'",
        (target_id,),
    ).fetchone()
    if not target:
        return HTMLResponse("<p>Target issue not found.</p>", status_code=404)

    parsed_ids = []
    for v in source_ids.split(","):
        v = v.strip()
        if v.isdigit():
            parsed_ids.append(int(v))

    today = date.today().isoformat()
    total_closed = 0
    for src_id in parsed_ids:
        if src_id == target_id:
            continue
        # Auto-close stale follow-ups on source before relink
        total_closed += auto_close_followups(
            conn, issue_id=src_id, reason="issue_merged",
            closed_by="issue_merge", before_date=today,
        )
        # Relink child activities to target (tag with source for dissolve)
        conn.execute(
            "UPDATE activity_log SET issue_id = ?, merged_from_issue_id = ? WHERE issue_id = ?",
            (target_id, src_id, src_id),
        )
        # Close source issue as merged
        conn.execute("""
            UPDATE activity_log
            SET issue_status = 'Closed',
                resolution_type = 'Duplicate',
                resolution_notes = 'Merged into ' || (SELECT issue_uid FROM activity_log WHERE id = ?),
                resolved_date = ?,
                merged_into_id = ?
            WHERE id = ? AND item_kind = 'issue'
        """, (target_id, today, target_id, src_id))

    conn.commit()
    return RedirectResponse(f"/issues/{target['issue_uid']}", status_code=303)


# ── Dissolve merge ─────────────────────────────────────────────────────────


@router.post("/issues/{target_id}/dissolve/{source_id}")
def dissolve_merge(
    target_id: int,
    source_id: int,
    conn=Depends(get_db),
):
    """Undo a merge: move activities back to source issue and reopen it."""
    # Verify source was merged into target
    source = conn.execute(
        "SELECT id, issue_uid, merged_into_id FROM activity_log WHERE id = ? AND item_kind = 'issue'",
        (source_id,),
    ).fetchone()
    if not source or source["merged_into_id"] != target_id:
        return HTMLResponse("<p>Source issue not found or not merged into this target.</p>", status_code=404)

    target = conn.execute(
        "SELECT issue_uid FROM activity_log WHERE id = ? AND item_kind = 'issue'",
        (target_id,),
    ).fetchone()
    if not target:
        return HTMLResponse("<p>Target issue not found.</p>", status_code=404)

    # Move activities back to source
    conn.execute(
        "UPDATE activity_log SET issue_id = ?, merged_from_issue_id = NULL WHERE issue_id = ? AND merged_from_issue_id = ?",
        (source_id, target_id, source_id),
    )

    # Reopen source issue
    conn.execute("""
        UPDATE activity_log
        SET merged_into_id = NULL,
            issue_status = 'Open',
            resolution_type = NULL,
            resolution_notes = NULL,
            resolved_date = NULL
        WHERE id = ? AND item_kind = 'issue'
    """, (source_id,))

    conn.commit()
    return HTMLResponse("", headers={"HX-Redirect": f"/issues/{target['issue_uid']}"})


# ── Merge relevance scoring ────────────────────────────────────────────────


def _score_merge_relevance(target: dict, candidate: dict) -> int:
    """Additive relevance score for merge suggestions. No hard gates."""
    score = 0

    # Same policy (strongest signal)
    if target.get("policy_id") and candidate.get("policy_id") == target["policy_id"]:
        score += 30

    # Same program
    if target.get("program_id") and candidate.get("program_id") == target["program_id"]:
        score += 20

    # Same location (project_name match)
    t_loc = target.get("project_name") or ""
    c_loc = candidate.get("project_name") or ""
    if t_loc and c_loc and t_loc == c_loc:
        score += 15

    # Same renewal term key
    rtk = target.get("renewal_term_key")
    if rtk and candidate.get("renewal_term_key") == rtk:
        score += 15

    # Fuzzy subject similarity
    t_subj = target.get("subject") or ""
    c_subj = candidate.get("subject") or ""
    if t_subj and c_subj:
        ratio = fuzz.token_sort_ratio(t_subj, c_subj)
        if ratio > 60:
            score += int((ratio - 60) / 2)  # 0–20 pts

    # Both renewal or both manual
    if bool(target.get("is_renewal_issue")) == bool(candidate.get("is_renewal_issue")):
        score += 5

    # Same severity
    if target.get("issue_severity") and target["issue_severity"] == candidate.get("issue_severity"):
        score += 3

    # Temporal proximity (within 14 days)
    t_days = target.get("days_open") or 0
    c_days = candidate.get("days_open") or 0
    gap = abs(t_days - c_days)
    if gap < 14:
        score += max(0, 7 - gap // 2)

    return score


# ── Mergeable issues for a target ───────────────────────────────────────────


@router.get("/issues/{issue_id}/mergeable", response_class=HTMLResponse)
def mergeable_issues(
    issue_id: int,
    request: Request,
    conn=Depends(get_db),
):
    """Return other open issues for the same client, sorted by relevance."""
    target = conn.execute("""
        SELECT a.id, a.client_id, a.policy_id, a.program_id, a.subject,
               a.issue_severity, a.is_renewal_issue, a.renewal_term_key,
               CAST(julianday('now') - julianday(a.activity_date) AS INTEGER) AS days_open,
               COALESCE(pr.name, pr2.name) AS project_name
        FROM activity_log a
        LEFT JOIN policies p ON p.id = a.policy_id
        LEFT JOIN projects pr ON pr.id = p.project_id
        LEFT JOIN programs pg ON pg.id = a.program_id
        LEFT JOIN projects pr2 ON pr2.id = pg.project_id
        WHERE a.id = ? AND a.item_kind = 'issue'
    """, (issue_id,)).fetchone()
    if not target:
        return HTMLResponse("")

    target = dict(target)

    rows = conn.execute("""
        SELECT a.id, a.issue_uid, a.subject, a.issue_severity, a.issue_status,
               a.is_renewal_issue, a.policy_id, a.program_id, a.renewal_term_key,
               CAST(julianday('now') - julianday(a.activity_date) AS INTEGER) AS days_open,
               (SELECT COUNT(*) FROM activity_log sub WHERE sub.issue_id = a.id) AS activity_count,
               COALESCE(pr.name, pr2.name) AS project_name
        FROM activity_log a
        LEFT JOIN policies p ON p.id = a.policy_id
        LEFT JOIN projects pr ON pr.id = p.project_id
        LEFT JOIN programs pg ON pg.id = a.program_id
        LEFT JOIN projects pr2 ON pr2.id = pg.project_id
        WHERE a.item_kind = 'issue'
          AND a.issue_id IS NULL
          AND a.merged_into_id IS NULL
          AND a.client_id = ?
          AND a.id != ?
          AND (a.issue_status IS NULL OR a.issue_status NOT IN ('Closed'))
        ORDER BY a.activity_date DESC
    """, (target["client_id"], issue_id)).fetchall()

    issues = [dict(r) for r in rows]
    for iss in issues:
        iss["relevance_score"] = _score_merge_relevance(target, iss)

    issues.sort(key=lambda x: x["relevance_score"], reverse=True)

    return templates.TemplateResponse(
        "issues/_merge_issue_list.html",
        {"request": request, "issues": issues, "target_id": issue_id},
    )


# ── Bulk delete ─────────────────────────────────────────────────────────────


@router.post("/issues/bulk-delete", response_class=HTMLResponse)
def bulk_delete_issues(
    request: Request,
    issue_ids: str = Form(""),
    conn=Depends(get_db),
):
    """Bulk-delete selected issues. Unlinks child activities."""
    parsed_ids = [int(v.strip()) for v in issue_ids.split(",") if v.strip().isdigit()]
    for issue_id in parsed_ids:
        conn.execute("UPDATE activity_log SET issue_id = NULL WHERE issue_id = ?", (issue_id,))
        conn.execute("DELETE FROM activity_log WHERE id = ? AND item_kind = 'issue'", (issue_id,))
    conn.commit()

    from policydb.web.routes.action_center import _issues_ctx
    ctx = _issues_ctx(conn)
    ctx["request"] = request
    return templates.TemplateResponse("action_center/_issues.html", ctx)


# ── Bulk resolve ────────────────────────────────────────────────────────────


@router.post("/issues/bulk-resolve", response_class=HTMLResponse)
def bulk_resolve_issues(
    request: Request,
    issue_ids: str = Form(""),
    resolution_type: str = Form("Completed"),
    conn=Depends(get_db),
):
    """Bulk-resolve selected issues."""
    parsed_ids = [int(v.strip()) for v in issue_ids.split(",") if v.strip().isdigit()]
    today = date.today().isoformat()
    total_closed = 0
    for issue_id in parsed_ids:
        conn.execute("""
            UPDATE activity_log
            SET issue_status = 'Resolved', resolution_type = ?, resolved_date = ?
            WHERE id = ? AND item_kind = 'issue'
        """, (resolution_type, today, issue_id))
        total_closed += auto_close_followups(
            conn, issue_id=issue_id, reason="issue_resolved", closed_by="bulk_resolve",
        )
    conn.commit()

    from policydb.web.routes.action_center import _issues_ctx
    ctx = _issues_ctx(conn)
    ctx["request"] = request
    resp = templates.TemplateResponse("action_center/_issues.html", ctx)
    if total_closed:
        resp.headers["HX-Trigger"] = f'{{"showToast": "{total_closed} follow-up(s) auto-closed: issues resolved"}}'
    return resp


# ── Bulk status update ──────────────────────────────────────────────────────


@router.post("/issues/bulk-status", response_class=HTMLResponse)
def bulk_status_issues(
    request: Request,
    issue_ids: str = Form(""),
    status: str = Form(""),
    conn=Depends(get_db),
):
    """Bulk-update status for selected issues."""
    parsed_ids = [int(v.strip()) for v in issue_ids.split(",") if v.strip().isdigit()]
    if not status or not parsed_ids:
        from policydb.web.routes.action_center import _issues_ctx
        ctx = _issues_ctx(conn)
        ctx["request"] = request
        return templates.TemplateResponse("action_center/_issues.html", ctx)

    placeholders = ",".join("?" * len(parsed_ids))
    conn.execute(
        f"UPDATE activity_log SET issue_status = ? WHERE id IN ({placeholders}) AND item_kind = 'issue'",
        [status] + parsed_ids,
    )

    total_closed = 0
    if status in ("Resolved", "Closed"):
        for issue_id in parsed_ids:
            total_closed += auto_close_followups(
                conn, issue_id=issue_id, reason="issue_resolved",
                closed_by="bulk_status_change",
            )
    conn.commit()

    from policydb.web.routes.action_center import _issues_ctx
    ctx = _issues_ctx(conn)
    ctx["request"] = request
    resp = templates.TemplateResponse("action_center/_issues.html", ctx)
    if total_closed:
        resp.headers["HX-Trigger"] = f'{{"showToast": "{total_closed} follow-up(s) auto-closed: issues {status.lower()}"}}'
    return resp


# ── Refresh renewal issue titles ────────────────────────────────────────────


@router.post("/issues/refresh-titles")
def refresh_titles(conn=Depends(get_db)):
    """Recompute all renewal issue titles from current data."""
    from policydb.renewal_issues import refresh_renewal_titles
    count = refresh_renewal_titles(conn)
    return {"ok": True, "updated": count}
