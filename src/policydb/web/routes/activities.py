"""Activity and renewal routes."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from policydb import config as cfg
from policydb.email_templates import followup_context, render_tokens
from policydb.queries import (
    get_activities,
    get_activity_by_id,
    get_all_followups,
    get_open_opportunities,
    get_renewal_pipeline,
    get_suggested_followups,
)
from policydb.web.app import get_db, templates

router = APIRouter()


@router.post("/activities/log", response_class=HTMLResponse)
def activity_log(
    request: Request,
    client_id: int = Form(...),
    policy_id: int = Form(0),
    activity_type: str = Form(...),
    subject: str = Form(...),
    details: str = Form(""),
    contact_person: str = Form(""),
    follow_up_date: str = Form(""),
    duration_minutes: str = Form(""),
    conn=Depends(get_db),
):
    def _int(v):
        try:
            return int(v) if str(v).strip() else None
        except ValueError:
            return None

    account_exec = cfg.get("default_account_exec", "Grant")
    cursor = conn.execute(
        """INSERT INTO activity_log
           (activity_date, client_id, policy_id, activity_type, contact_person, subject, details, follow_up_date, account_exec, duration_minutes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date.today().isoformat(), client_id, policy_id or None, activity_type,
         contact_person or None, subject, details or None,
         follow_up_date or None, account_exec, _int(duration_minutes)),
    )
    conn.commit()
    # Return the new activity row as HTMX partial
    row = conn.execute(
        """SELECT a.*, c.name AS client_name, p.policy_uid
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           LEFT JOIN policies p ON a.policy_id = p.id
           WHERE a.id = ?""",
        (cursor.lastrowid,),
    ).fetchone()
    a = dict(row)
    return templates.TemplateResponse("activities/_activity_row.html", {
        "request": request,
        "a": a,
    })


@router.post("/activities/{activity_id}/complete", response_class=HTMLResponse)
def activity_complete(request: Request, activity_id: int, conn=Depends(get_db)):
    conn.execute(
        "UPDATE activity_log SET follow_up_done=1 WHERE id=?", (activity_id,)
    )
    conn.commit()
    return HTMLResponse("")


@router.post("/activities/{activity_id}/snooze", response_class=HTMLResponse)
def activity_snooze(request: Request, activity_id: int, days: int = 7, conn=Depends(get_db)):
    conn.execute(
        "UPDATE activity_log SET follow_up_date = date(follow_up_date, ?) WHERE id=?",
        (f"+{days} days", activity_id),
    )
    conn.commit()
    row = conn.execute(
        """SELECT a.*, c.name AS client_name, p.policy_uid, p.policy_type, p.carrier, p.project_name,
                  CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue,
                  NULL AS contact_email, NULL AS internal_cc
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           LEFT JOIN policies p ON a.policy_id = p.id
           WHERE a.id = ?""",
        (activity_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("")
    r = dict(row)
    today = date.today().isoformat()
    r["_is_overdue"] = r["follow_up_date"] < today
    return templates.TemplateResponse("followups/_row.html", {"request": request, "r": r, "today": today})


@router.post("/activities/{activity_id}/reschedule", response_class=HTMLResponse)
def activity_reschedule(request: Request, activity_id: int, new_date: str = Form(...), conn=Depends(get_db)):
    """Reschedule an activity follow-up to a specific date."""
    conn.execute(
        "UPDATE activity_log SET follow_up_date = ? WHERE id=?",
        (new_date, activity_id),
    )
    conn.commit()
    row = conn.execute(
        """SELECT a.*, c.name AS client_name, p.policy_uid, p.policy_type, p.carrier, p.project_name,
                  CAST(julianday('now') - julianday(a.follow_up_date) AS INTEGER) AS days_overdue,
                  NULL AS contact_email, NULL AS internal_cc
           FROM activity_log a
           JOIN clients c ON a.client_id = c.id
           LEFT JOIN policies p ON a.policy_id = p.id
           WHERE a.id = ?""",
        (activity_id,),
    ).fetchone()
    if not row:
        return HTMLResponse("")
    r = dict(row)
    today = date.today().isoformat()
    r["_is_overdue"] = r["follow_up_date"] < today
    return templates.TemplateResponse("followups/_row.html", {"request": request, "r": r, "today": today})


def _add_mailto_subjects(rows: list, subject_tpl: str) -> list:
    """Convert rows to dicts and add rendered mailto_subject to each."""
    result = []
    for r in rows:
        d = dict(r) if not isinstance(r, dict) else r
        d["mailto_subject"] = render_tokens(subject_tpl, followup_context(d))
        result.append(d)
    return result


def _followups_ctx(conn, window: int, activity_type: str, q: str) -> dict:
    excluded = cfg.get("renewal_statuses_excluded", [])
    overdue_raw, upcoming_raw = get_all_followups(conn, window=window)
    suggested = get_suggested_followups(conn, excluded_statuses=excluded)
    if activity_type:
        overdue_raw  = [r for r in overdue_raw  if r["activity_type"] == activity_type]
        upcoming_raw = [r for r in upcoming_raw if r["activity_type"] == activity_type]
    if q:
        q_lower = q.lower()
        overdue_raw  = [r for r in overdue_raw  if q_lower in r["client_name"].lower()]
        upcoming_raw = [r for r in upcoming_raw if q_lower in r["client_name"].lower()]
        suggested    = [r for r in suggested    if q_lower in r["client_name"].lower()]
    subject_tpl = cfg.get("email_subject_followup", "Re: {{client_name}} — {{policy_type}} — {{subject}}")
    overdue  = _add_mailto_subjects(overdue_raw,  subject_tpl)
    upcoming = _add_mailto_subjects(upcoming_raw, subject_tpl)
    # Split upcoming into triage groups
    today_str = date.today().isoformat()
    tomorrow_str = (date.today() + timedelta(days=1)).isoformat()
    today_items = [r for r in upcoming if r.get("follow_up_date") == today_str]
    tomorrow_items = [r for r in upcoming if r.get("follow_up_date") == tomorrow_str]
    later_items = [r for r in upcoming if r.get("follow_up_date", "") > tomorrow_str]
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
        "today": today_str,
        "activity_types": cfg.get("activity_types", []),
        "renewal_statuses": cfg.get("renewal_statuses", []),
    }


@router.get("/followups", response_class=HTMLResponse)
def followups_page(
    request: Request,
    window: int = 30,
    activity_type: str = "",
    q: str = "",
    conn=Depends(get_db),
):
    ctx = _followups_ctx(conn, window, activity_type, q)
    ctx.update({"request": request, "active": "followups"})
    return templates.TemplateResponse("followups.html", ctx)


@router.get("/followups/results", response_class=HTMLResponse)
def followups_results(
    request: Request,
    window: int = 30,
    activity_type: str = "",
    q: str = "",
    conn=Depends(get_db),
):
    """HTMX partial: return just the results tables for filter updates."""
    ctx = _followups_ctx(conn, window, activity_type, q)
    ctx["request"] = request
    return templates.TemplateResponse("followups/_results.html", ctx)


@router.get("/activities", response_class=HTMLResponse)
def activity_list(
    request: Request,
    days: int = 90,
    activity_type: str = "",
    client_id: int = 0,
    conn=Depends(get_db),
):
    rows = [dict(r) for r in get_activities(
        conn, days=days,
        client_id=client_id or None,
        activity_type=activity_type or None,
    )]
    overdue, _ = get_all_followups(conn, window=0)
    all_clients = conn.execute(
        "SELECT id, name FROM clients WHERE archived=0 ORDER BY name"
    ).fetchall()
    return templates.TemplateResponse("activities/list.html", {
        "request": request,
        "active": "activities",
        "activities": rows,
        "overdue": overdue,
        "days": days,
        "activity_type": activity_type,
        "client_id": client_id,
        "activity_types": cfg.get("activity_types", []),
        "all_clients": [dict(c) for c in all_clients],
    })


_URGENCY_ORDER = ["EXPIRED", "URGENT", "WARNING", "UPCOMING", "OK"]


@router.get("/renewals/calendar", response_class=HTMLResponse)
def renewals_calendar(request: Request, conn=Depends(get_db)):
    rows = get_renewal_pipeline(conn, window_days=365)

    months: dict = defaultdict(list)
    for p in rows:
        d = dict(p)
        client_row = conn.execute(
            "SELECT id FROM clients WHERE name=?", (d["client_name"],)
        ).fetchone()
        d["client_id"] = client_row["id"] if client_row else 0
        months[d["expiration_date"][:7]].append(d)

    calendar = []
    for month_key in sorted(months.keys()):
        policies = months[month_key]
        urgencies = [p["urgency"] for p in policies]
        worst = min(urgencies, key=lambda u: _URGENCY_ORDER.index(u) if u in _URGENCY_ORDER else 99)
        calendar.append({
            "month_key": month_key,
            "month_label": datetime.strptime(month_key, "%Y-%m").strftime("%B %Y"),
            "policies": policies,
            "policy_count": len(policies),
            "total_premium": sum(p["premium"] or 0 for p in policies),
            "worst_urgency": worst,
        })

    return templates.TemplateResponse("renewals_calendar.html", {
        "request": request,
        "active": "renewals",
        "calendar": calendar,
    })


@router.get("/renewals/export")
def renewals_export(window: int = 180, fmt: str = "xlsx", conn=Depends(get_db)):
    from fastapi.responses import Response
    from policydb.exporter import export_renewals_csv, export_renewals_xlsx
    today = date.today().isoformat()
    if fmt == "xlsx":
        content = export_renewals_xlsx(conn, window_days=window)
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="renewals_{today}.xlsx"'},
        )
    content = export_renewals_csv(conn, window_days=window)
    return Response(
        content=content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="renewals_{today}.csv"'},
    )


_RENEWAL_SORT_FIELDS = {
    "client_name", "carrier", "expiration_date", "days_to_renewal",
    "premium", "renewal_status", "follow_up_date",
}

@router.get("/renewals", response_class=HTMLResponse)
def renewals(request: Request, window: int = 180, urgency: str = "", status: str = "",
             sort: str = "expiration_date", dir: str = "asc", conn=Depends(get_db)):
    excluded = cfg.get("renewal_statuses_excluded", [])
    rows = get_renewal_pipeline(
        conn,
        window_days=window,
        urgency=urgency or None,
        renewal_status=status or None,
        excluded_statuses=excluded,
    )

    # Attach client_id for linking, then milestone progress
    from policydb.email_templates import policy_context as _policy_ctx, render_tokens as _render_tokens
    _subj_tpl = cfg.get("email_subject_policy", "Re: {{client_name}} — {{policy_type}}")
    pipeline = []
    for p in rows:
        d = dict(p)
        client_row = conn.execute(
            "SELECT id FROM clients WHERE name=?", (d["client_name"],)
        ).fetchone()
        d["client_id"] = client_row["id"] if client_row else 0
        _mail_ctx = _policy_ctx(conn, d["policy_uid"])
        d["mailto_subject"] = _render_tokens(_subj_tpl, _mail_ctx)
        pipeline.append(d)
    from policydb.web.routes.policies import _attach_milestone_progress, _attach_readiness_score
    pipeline = _attach_readiness_score(conn, _attach_milestone_progress(conn, pipeline))

    sort_field = sort if sort in _RENEWAL_SORT_FIELDS else "expiration_date"
    reverse = dir == "desc"
    pipeline.sort(
        key=lambda r: (r.get(sort_field) is None, r.get(sort_field) or ""),
        reverse=reverse,
    )

    open_opportunities = get_open_opportunities(conn)

    return templates.TemplateResponse("renewals.html", {
        "request": request,
        "active": "renewals",
        "rows": pipeline,
        "window": window,
        "urgency": urgency,
        "status": status,
        "sort": sort_field,
        "dir": dir,
        "renewal_statuses": cfg.get("renewal_statuses"),
        "open_opportunities": open_opportunities,
        "today": date.today().isoformat(),
    })


# ── Bulk triage actions ──────────────────────────────────────────────────────

@router.post("/followups/bulk-reschedule", response_class=HTMLResponse)
def bulk_reschedule(
    request: Request,
    ids: str = Form(...),
    new_date: str = Form(...),
    conn=Depends(get_db),
):
    """Bulk reschedule selected follow-ups to a specific date."""
    for item in ids.split(","):
        item = item.strip()
        if not item:
            continue
        source, item_id = item.split("-", 1)
        if source == "activity":
            conn.execute("UPDATE activity_log SET follow_up_date=? WHERE id=?", (new_date, int(item_id)))
        elif source == "policy":
            conn.execute("UPDATE policies SET follow_up_date=? WHERE policy_uid=?", (new_date, item_id))
    conn.commit()
    # Return refreshed results partial
    window = 30
    ctx = _followups_ctx(conn, window, "", "")
    ctx["request"] = request
    return templates.TemplateResponse("followups/_results.html", ctx)


@router.post("/followups/bulk-complete", response_class=HTMLResponse)
def bulk_complete(
    request: Request,
    ids: str = Form(...),
    conn=Depends(get_db),
):
    """Bulk complete/clear selected follow-ups."""
    for item in ids.split(","):
        item = item.strip()
        if not item:
            continue
        source, item_id = item.split("-", 1)
        if source == "activity":
            conn.execute("UPDATE activity_log SET follow_up_done=1 WHERE id=?", (int(item_id),))
        elif source == "policy":
            conn.execute("UPDATE policies SET follow_up_date=NULL WHERE policy_uid=?", (item_id,))
    conn.commit()
    # Return refreshed results partial
    window = 30
    ctx = _followups_ctx(conn, window, "", "")
    ctx["request"] = request
    return templates.TemplateResponse("followups/_results.html", ctx)
